"""Run eval questions in parallel with Ray: several worker processes, one shared rate limiter (4.3).

    uv sync --group eval                                                  # installs Ray
    uv run python -m evals.run --suite bird --provider gemini --workers 4
    uv run python -m evals.rescore docs/eval-reports/<run>.json --workers 4

How it works:
- **Workers** are Ray *actors*: Python objects living in their own process. Each one builds its
  own Pipelines once (DB connections and LLM clients can't be sent between processes) and then
  answers one question at a time with the same `evaluate_question` as the sequential run, so a
  verdict means the same thing either way.
- **The rate limiter** is one more actor that every worker asks before each LLM call
  (evals/ratelimit.py). An actor handles one message at a time, so two workers can never book
  the same slot: no locks needed.
- **Tokens per question:** each worker has its own AuditCapture (one per process), and a worker
  runs one question at a time, so the "llm call" lines it adds up belong to that question.

Ray is optional (the `eval` dependency group): evals/run.py and evals/rescore.py import this
module only when --workers is more than 1.
"""

import logging
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
import ray
from ray.util import ActorPool

from evals.golden_set import GoldenQuestion
from evals.ratelimit import Limits, RateLimitedLLM, RateLimiter, Reservation
from evals.report import Outcome
from evals.rescore import database_settings, rescore_outcome
from evals.run import AskFn, AuditCapture, AuditedAsk, evaluate_question
from querymind.agent.schemas import AgentReply
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.executor import QueryResult
from querymind.db.schema import introspect_schema
from querymind.llm.base import LLMProvider

type LLMWrapper = Callable[[LLMProvider], LLMProvider]
#: Builds the ask function inside a worker; gets the wrapper that adds the rate limiter.
type AskFactory = Callable[[LLMWrapper], AskFn]


@contextmanager
def ray_running(workers: int) -> Iterator[float]:
    """Start Ray on this machine for the run (if it isn't already) and stop it afterwards.

    Yields how long starting took, so a report can keep start-up apart from the work itself.
    """
    if ray.is_initialized():  # e.g. the tests start Ray once for many runs
        yield 0.0
        return
    started = time.perf_counter()
    ray.init(
        num_cpus=workers,
        include_dashboard=False,  # the web dashboard: not needed, and slow to start
        logging_level=logging.WARNING,
    )
    try:
        yield time.perf_counter() - started
    finally:
        ray.shutdown()


# num_cpus=0: these actors mostly wait (for the LLM, for Postgres), they don't compute. Ray then
# places them without reserving a CPU each.
@ray.remote(num_cpus=0)
class SharedLimiter:
    """The one RateLimiter all workers book with, in its own process (its clock is the only one)."""

    def __init__(self, limits: Limits) -> None:
        self._limiter = RateLimiter(limits)

    def reserve(self, tokens: int) -> Reservation:
        return self._limiter.reserve(tokens)

    def settle(self, reservation_id: int, tokens: int) -> None:
        self._limiter.settle(reservation_id, tokens)


class RemoteLimiter:
    """The `Limiter` interface over the shared actor: each call is a message to its process."""

    def __init__(self, actor: Any) -> None:
        self._actor = actor

    def reserve(self, tokens: int) -> Reservation:
        reservation: Reservation = ray.get(self._actor.reserve.remote(tokens))
        return reservation

    def settle(self, reservation_id: int, tokens: int) -> None:
        self._actor.settle.remote(reservation_id, tokens)  # no answer needed: don't wait for it


@ray.remote(num_cpus=0)
class QuestionWorker:
    """One worker process: its own pipelines, its own AuditCapture, one question at a time."""

    def __init__(
        self,
        make_ask: AskFactory,
        limiter: Any,
        limits: Limits,
        retries: int,
        backoff_seconds: float,
    ) -> None:
        self._capture = AuditCapture.attach()
        self._retries = retries
        self._backoff = backoff_seconds
        remote = RemoteLimiter(limiter)

        def wrap(llm: LLMProvider) -> LLMProvider:
            return RateLimitedLLM(llm, remote, limits=limits, used=self._tokens_so_far)

        self._ask = make_ask(wrap)

    def _tokens_so_far(self) -> tuple[int, int]:
        return self._capture.last.input_tokens, self._capture.last.output_tokens

    def run(self, item: GoldenQuestion, expected: QueryResult) -> Outcome:
        def ask(question: GoldenQuestion) -> AgentReply:
            self._capture.last = AuditedAsk()  # a fresh count for each try
            return self._ask(question)

        return evaluate_question(
            item,
            ask=ask,
            reference=lambda _item: expected,
            retries=self._retries,
            backoff_seconds=self._backoff,
            last_audit=lambda: self._capture.last,
        )


def evaluate_parallel(
    items: Sequence[GoldenQuestion],
    *,
    make_ask: AskFactory,
    references: Mapping[str, QueryResult],
    workers: int,
    limits: Limits,
    retries: int = 2,
    backoff_seconds: float = 30.0,
    on_outcome: Callable[[int, Outcome], None] = lambda _number, _outcome: None,
) -> list[Outcome]:
    """`evaluate` with `workers` questions at a time (inside `ray_running`).

    Outcomes come back in the questions' order, so reports stay comparable with sequential
    runs. `on_outcome` is called as each question finishes (finishing order), numbered 1, 2...
    """
    limiter = SharedLimiter.remote(limits)  # type: ignore[attr-defined]
    pool = ActorPool(
        [
            QuestionWorker.remote(  # type: ignore[attr-defined]
                make_ask, limiter, limits, retries, backoff_seconds
            )
            for _ in range(workers)
        ]
    )
    done: dict[str, Outcome] = {}
    jobs = [(item, references[item.id]) for item in items]
    finished = pool.map_unordered(lambda actor, job: actor.run.remote(*job), jobs)
    for number, outcome in enumerate(finished, start=1):
        done[outcome.item.id] = outcome
        on_outcome(number, outcome)
    return [done[item.id] for item in items]


@ray.remote(num_cpus=0)
class RescoreWorker:
    """Re-runs saved SQL; keeps one connection per database it has seen."""

    def __init__(self, settings: Settings, max_rows: int) -> None:
        self._settings = settings
        self._max_rows = max_rows
        self._open: dict[str, tuple[psycopg.Connection, frozenset[str], Settings]] = {}

    def run(self, index: int, outcome: Outcome) -> tuple[int, Outcome]:
        """Rescore one outcome; `index` comes back with it, to put the answers back in order."""
        database = outcome.database or self._settings.postgres_db
        if database not in self._open:
            settings = database_settings(self._settings, database, self._max_rows)
            conn = connect_readonly(settings)
            tables = introspect_schema(conn, sample_rows=0).table_names
            self._open[database] = (conn, tables, settings)
        conn, tables, settings = self._open[database]
        return index, rescore_outcome(outcome, conn=conn, tables=tables, settings=settings)


def rescore_parallel(
    outcomes: Sequence[Outcome], settings: Settings, *, max_rows: int, workers: int
) -> list[Outcome]:
    """`rescore_outcome` for each of `outcomes`, `workers` at a time (inside `ray_running`).

    Same order as `outcomes`. Only pass answered ones (evals/rescore.py decides which).

    `map_unordered`, not `map`: the ordered `map` hands a worker its next question only once its
    result is next in line, so one slow query (2.3 s in BIRD-100) made every worker wait.
    """
    pool = ActorPool(
        [RescoreWorker.remote(settings, max_rows) for _ in range(workers)]  # type: ignore[attr-defined]
    )
    finished = pool.map_unordered(
        lambda actor, job: actor.run.remote(*job), list(enumerate(outcomes))
    )
    done = dict(finished)  # index → rescored outcome (ids aren't always unique in a saved report)
    return [done[index] for index in range(len(outcomes))]
