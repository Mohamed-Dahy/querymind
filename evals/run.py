"""Run a golden set through QueryMind and measure execution accuracy.

    uv run python -m evals.run --suite golden                       # all 30 questions
    uv run python -m evals.run --suite golden --limit 5             # a quick check
    uv run python -m evals.run --suite golden --ids e01_customer_count m07_revenue_1997
    uv run python -m evals.run --suite golden --report docs/eval-reports/phase1-baseline.md
    uv run python -m evals.run --suite golden --max-attempts 1     # no self-correction (ablation)
    uv run python -m evals.run --suite business                     # questions in business terms
    uv run python -m evals.run --suite business --no-semantic-layer # same, without definitions
    uv run python -m evals.run --suite chinook                      # the music store (3.2)
    uv run python -m evals.run --suite chinook --schema-retrieval off  # whole schema (3.3)
    uv run python -m evals.run --suite bird --limit 20 --provider gemini   # BIRD benchmark (4.1)
    uv run python -m evals.run --suite bird --no-evidence           # without BIRD's hints
    uv run python -m evals.run --suite bird --references-only       # only the gold SQL, no LLM
    uv run python -m evals.run --suite bird --provider gemini --workers 4   # in parallel (4.3)

For each question: QueryMind writes and runs its own SQL (the real pipeline, real LLM), the
reference SQL runs too, and the two RESULTS are compared (evals/compare.py). The score is the
share of questions whose results match. Uses real LLM calls: not run in CI.

The BIRD suite (4.1) spans 11 databases: each question runs in its own (bird_<db_id>), with one
Pipeline per database, BIRD's protocol (whole schema, no definitions of ours, evals/bird.py),
and the evidence hint after the question unless --no-evidence.

With --workers N (4.3, needs `uv sync --group eval`), N Ray workers answer questions at the same
time and book every LLM call with one shared rate limiter (evals/parallel.py) instead of --delay.
"""

import argparse
import logging
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from evals.bird import (
    SUBSET_FILE,
    TIER_OF,
    BirdError,
    bird_settings,
    interleave,
    load_bird_questions,
    load_subset,
    to_golden,
)
from evals.compare import bird_ex, compare_results
from evals.golden_set import GOLDEN_DIR, GoldenQuestion, load_golden_set
from evals.ratelimit import FREE_TIER, Limits
from evals.report import Outcome, modes_text, status, summarize_outcomes, write_reports
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest, Refusal
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError, QueryError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import introspect_schema
from querymind.llm import (
    LLMError,
    LLMProvider,
    LLMProviderError,
    create_embedding_provider,
    create_llm_provider,
)
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

SUITES = {
    "golden": GOLDEN_DIR / "northwind.yaml",
    "business": GOLDEN_DIR / "northwind_business.yaml",  # needs the semantic layer (3.1)
    "chinook": GOLDEN_DIR / "chinook.yaml",  # the music store, next to Northwind (3.2)
}
BIRD_SUITE = "bird"  # the BIRD Mini-Dev benchmark (4.1): 11 databases, not a YAML file

# ask() gets the whole item, not just its text: a BIRD question picks its database by its id.
type AskFn = Callable[[GoldenQuestion], AgentReply]
type ReferenceFn = Callable[[GoldenQuestion], QueryResult]


@dataclass(frozen=True)
class AuditedAsk:
    """What the logs said about one question: from the pipeline's audit line, the last SQL
    tried, how many queries and the schema the LLM was shown; from the "llm call" lines, how
    many LLM calls it took and their tokens (4.2)."""

    sql: str | None = None
    sql_attempts: int = 0
    schema_mode: str | None = None
    schema_tables: int = 0
    schema_tokens: int = 0
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def evaluate_question(
    item: GoldenQuestion,
    *,
    ask: AskFn,
    reference: ReferenceFn,
    retries: int = 2,
    backoff_seconds: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    last_audit: Callable[[], AuditedAsk] = AuditedAsk,
) -> Outcome:
    """Ask one golden question and compare QueryMind's result with the reference result.

    An LLMProviderError (usually a rate limit) or a database that can't be reached is retried
    after a pause; if it never works, the question is *not scored*: an outage says nothing about
    the agent's accuracy. Every other failure (unsafe SQL, a query error, unusable LLM output)
    counts as a wrong answer, and so does asking back or refusing: every golden question is
    clear and only reads data. `last_audit` tells what a failed question tried (AuditCapture).
    """
    started = time.perf_counter()
    for attempt in range(1, retries + 2):
        try:
            answer = ask(item)
        except (LLMProviderError, DatabaseUnavailableError) as exc:
            if attempt <= retries:
                sleep(backoff_seconds * attempt)
                continue
            return _failed(item, exc, started, attempt, last_audit(), scored=False)
        except (UnsafeSqlError, QueryError, LLMError, ValueError) as exc:
            return _failed(item, exc, started, attempt, last_audit())

        if not isinstance(answer, AskResult):
            return _with_audit(_no_answer(item, answer, started, attempt), last_audit())
        expected = reference(item)
        comparison = compare_results(expected=expected, actual=answer.result, ordered=item.ordered)
        official = bird_ex(expected=expected, actual=answer.result)
        outcome = Outcome(
            item=item,
            passed=comparison.match,
            strict=comparison.strict,
            reason=comparison.reason,
            error=None,
            sql=answer.sql,
            seconds=time.perf_counter() - started,
            provider_attempts=attempt,
            sql_attempts=answer.sql_attempts,
            confidence=answer.confidence.level,
            ex=official.match,
            ex_reason=official.reason,
        )
        return _with_audit(outcome, last_audit())
    raise AssertionError("unreachable: the loop always returns")


def _with_audit(outcome: Outcome, audited: AuditedAsk) -> Outcome:
    """Add what the logs say: the schema the LLM was shown (3.3) and what it cost (4.2)."""
    return replace(
        outcome,
        schema_mode=audited.schema_mode,
        schema_tables=audited.schema_tables,
        schema_tokens=audited.schema_tokens,
        llm_calls=audited.llm_calls,
        input_tokens=audited.input_tokens,
        output_tokens=audited.output_tokens,
    )


def _no_answer(
    item: GoldenQuestion, reply: ClarificationRequest | Refusal, started: float, attempt: int
) -> Outcome:
    """A clarifying question or a refusal instead of an answer: wrong for a golden question."""
    if isinstance(reply, ClarificationRequest):
        reason = f"asked back instead of answering: {reply.clarification.question}"
    else:
        reason = f"refused: {reply.message}"
    return Outcome(
        item=item,
        passed=False,
        strict=False,
        reason=reason,
        error=None,
        sql=None,
        seconds=time.perf_counter() - started,
        provider_attempts=attempt,
        sql_attempts=0,
        confidence=None,
    )


def _failed(
    item: GoldenQuestion,
    exc: Exception,
    started: float,
    attempt: int,
    audited: AuditedAsk,
    *,
    scored: bool = True,
) -> Outcome:
    message = exc.reason if isinstance(exc, UnsafeSqlError) else str(exc)
    outcome = Outcome(
        item=item,
        passed=False,
        strict=False,
        reason=message,
        error=type(exc).__name__,
        sql=audited.sql,
        seconds=time.perf_counter() - started,
        provider_attempts=attempt,
        sql_attempts=audited.sql_attempts,
        confidence=None,
        scored=scored,
    )
    return _with_audit(outcome, audited)


def evaluate(
    items: Sequence[GoldenQuestion],
    *,
    ask: AskFn,
    reference: ReferenceFn,
    delay_seconds: float = 0.0,
    retries: int = 2,
    backoff_seconds: float = 30.0,
    on_outcome: Callable[[int, Outcome], None] = lambda _number, _outcome: None,
    sleep: Callable[[float], None] = time.sleep,
    last_audit: Callable[[], AuditedAsk] = AuditedAsk,
) -> list[Outcome]:
    """Run every question in order. `delay_seconds` spaces calls out to respect rate limits."""
    outcomes = []
    for number, item in enumerate(items, start=1):
        if number > 1 and delay_seconds:
            sleep(delay_seconds)
        outcome = evaluate_question(
            item,
            ask=ask,
            reference=reference,
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
            last_audit=last_audit,
        )
        outcomes.append(outcome)
        on_outcome(number, outcome)
    return outcomes


@dataclass(frozen=True)
class SettingsFor:
    """The settings each question runs with: a BIRD question gets its own database (4.1).

    A small class rather than a closure so that Ray can send it to worker processes (4.3).
    """

    base: Settings
    databases: dict[str, str]  # question id → Postgres database; missing = the one in .env

    def __call__(self, item: GoldenQuestion) -> Settings:
        database = self.databases.get(item.id)
        return bird_settings(self.base, database) if database else self.base


@dataclass(frozen=True)
class PipelineAsk:
    """Builds the real ask(): one Pipeline per database, all sharing one LLM client.

    Called once in the main process (sequential run) or once inside each Ray worker (4.3):
    there `wrap_llm` puts the shared rate limiter around the LLM.
    """

    items: tuple[GoldenQuestion, ...]
    settings_for: SettingsFor

    def __call__(self, wrap_llm: Callable[[LLMProvider], LLMProvider] = lambda llm: llm) -> AskFn:
        pipelines = _pipelines(self.items, self.settings_for, wrap_llm=wrap_llm)

        def ask(item: GoldenQuestion) -> AgentReply:
            pipeline = pipelines[self.settings_for(item).postgres_db]
            return pipeline.ask(item.question, summarize_answer=False)

        return ask


@dataclass(frozen=True)
class Suite:
    """The questions of one run, and the database each one is about."""

    name: str
    label: str  # for the report, e.g. "northwind, version 2"
    items: list[GoldenQuestion]
    databases: dict[str, str]  # question id → Postgres database; empty = the one in .env


def load_suite(name: str, *, full: bool = False, evidence: bool = True) -> Suite:
    """A golden YAML set, or BIRD: the fixed 100-question subset (all 500 with `full`).

    Raises:
        BirdError: BIRD isn't downloaded yet (data/seed_bird.py) or the subset file is invalid.
    """
    if name != BIRD_SUITE:
        golden = load_golden_set(SUITES[name])
        return Suite(name, f"{golden.database}, version {golden.version}", [*golden.questions], {})
    questions = load_bird_questions()
    chosen = interleave(questions if full else load_subset(questions))  # --limit: all databases
    source = "all 500" if full else f"the fixed subset, {SUBSET_FILE.name}"
    label = f"BIRD Mini-Dev, {source}, evidence {'on' if evidence else 'off'}"
    return Suite(
        name,
        label,
        [to_golden(q, evidence=evidence) for q in chosen],
        {q.id: q.database for q in chosen},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure QueryMind's execution accuracy.")
    parser.add_argument("--suite", choices=[*SUITES, BIRD_SUITE], default="golden")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--ids", nargs="+", help="only these question ids")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="override LLM_PROVIDER")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between questions")
    parser.add_argument("--retries", type=int, default=2, help="retries after a rate limit")
    parser.add_argument(
        "--backoff", type=float, default=60.0, help="seconds to wait before retry 1 (x2, x3...)"
    )
    parser.add_argument(
        "--max-attempts", type=int, help="override MAX_SQL_ATTEMPTS (1 = no self-correction)"
    )
    parser.add_argument(
        "--no-semantic-layer",
        action="store_true",
        help="leave the business definitions out of the prompt (ablation)",
    )
    parser.add_argument(
        "--schema-retrieval",
        choices=["auto", "on", "off"],
        help="override SCHEMA_RETRIEVAL (off = the whole schema, the 3.3 ablation)",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=["vector", "keyword", "hybrid", "hybrid_rerank"],
        help="override RETRIEVAL_MODE (how tables are ranked, 3.4)",
    )
    parser.add_argument(
        "--few-shot",
        choices=["on", "off"],
        help="off = no few-shot examples in the prompt (the 3.5 ablation); on = FEW_SHOT_EXAMPLES",
    )
    parser.add_argument(
        "--no-evidence", action="store_true", help="bird: ask without BIRD's hints (ablation)"
    )
    parser.add_argument(
        "--full", action="store_true", help="bird: all 500 questions, not the fixed 100"
    )
    parser.add_argument(
        "--references-only",
        action="store_true",
        help="only run the reference SQL (no LLM): checks the suite itself",
    )
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="questions at the same time, with Ray and a shared rate limiter (4.3); 1 = in turn",
    )
    parser.add_argument("--rpm", type=int, help="--workers: the provider's requests per minute")
    parser.add_argument("--tpm", type=int, help="--workers: the provider's tokens per minute")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        suite = load_suite(args.suite, full=args.full, evidence=not args.no_evidence)
    except BirdError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    items = [q for q in suite.items if not args.ids or q.id in args.ids][: args.limit]
    if not items:
        print("Error: no questions match --ids / --limit", file=sys.stderr)
        return 1
    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        if args.max_attempts:
            settings = settings.model_copy(update={"max_sql_attempts": args.max_attempts})
        if args.no_semantic_layer:
            settings = settings.model_copy(update={"semantic_layer_paths": []})
        if args.schema_retrieval:
            settings = settings.model_copy(update={"schema_retrieval": args.schema_retrieval})
        if args.retrieval_mode:
            settings = settings.model_copy(update={"retrieval_mode": args.retrieval_mode})
        if args.few_shot == "off":
            settings = settings.model_copy(update={"few_shot_examples": 0})

        settings_for = SettingsFor(settings, suite.databases)
        references, reference_failures = run_references(items, settings_for)
        if args.references_only:
            return _print_references(items, references, reference_failures)
        items = [item for item in items if item.id not in reference_failures]
        model, _ = settings.llm_credentials()
        make_ask = PipelineAsk(tuple(items), settings_for)
        # Built here even with --workers (each worker builds its own): a config or database
        # problem then shows as a clear error before the run, not as a crash inside a worker.
        answer = make_ask()
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1
    limits = _limits(settings.llm_provider, rpm=args.rpm, tpm=args.tpm)
    shown = settings_for(items[0])  # BIRD: what every one of its databases gets
    database_count = len({settings_for(item).postgres_db for item in items})
    databases = f"{database_count} databases, " if database_count > 1 else ""
    pace = (
        f"{args.workers} Ray workers, {_limits_text(limits)}"
        if args.workers > 1
        else f"one at a time, {args.delay:g} s apart"
    )
    print(
        f"Evaluating {len(items)} questions with {settings.llm_provider} / {model}, "
        f"{databases}max {shown.max_sql_attempts} SQL attempts, "
        f"semantic layer: {_layers(shown)}, schema retrieval: {shown.schema_retrieval}, "
        f"few-shot: {shown.few_shot_examples or 'off'}; {pace}\n"
    )
    if reference_failures:
        print(f"Reference SQL failed, not asked: {', '.join(sorted(reference_failures))}\n")
    started = time.perf_counter()
    startup = 0.0

    def progress(number: int, outcome: Outcome) -> None:
        print(_progress_line(number, len(items), outcome))

    if args.workers > 1:
        try:
            outcomes, startup = _evaluate_with_ray(
                items,
                make_ask,
                references,
                workers=args.workers,
                limits=limits,
                retries=args.retries,
                backoff_seconds=args.backoff,
                on_outcome=progress,
            )
        except ModuleNotFoundError as exc:
            if exc.name != "ray":
                raise
            print("Error: --workers needs Ray: uv sync --group eval", file=sys.stderr)
            return 1
    else:
        capture = AuditCapture.attach()

        def ask(item: GoldenQuestion) -> AgentReply:
            capture.last = AuditedAsk()
            return answer(item)

        outcomes = evaluate(
            items,
            ask=ask,
            last_audit=lambda: capture.last,
            reference=lambda item: references[item.id],
            delay_seconds=args.delay,
            retries=args.retries,
            backoff_seconds=args.backoff,
            on_outcome=progress,
        )
    wall = time.perf_counter() - started
    outcomes = [replace(o, database=settings_for(o.item).postgres_db) for o in outcomes]

    summary = summarize_outcomes(outcomes)
    tier_names: dict[str, str] = (
        {tier: name for name, tier in TIER_OF.items()} if suite.databases else {}
    )  # BIRD: easy → simple...
    print(f"\nQueryMind match: {summary.accuracy}   strict: {summary.strict}")
    print(f"BIRD EX (official): {summary.ex}")
    if summary.corrected:
        print(f"Passed after self-correction: {', '.join(summary.corrected)}")
    print("  " + "   ".join(
        f"{tier_names.get(tier, tier)}: {match} (EX {ex.passed}/{ex.total})"
        for tier, (match, ex) in summary.by_tier.items()
    ))  # fmt: skip
    print(f"  schema shown: {modes_text(summary)}, mean ~{summary.schema_tokens:,.0f} tokens")
    print(
        f"  per question: {summary.llm_calls:.1f} LLM calls, ~{summary.input_tokens:,.0f} tokens"
        f" in / ~{summary.output_tokens:,.0f} out; {summary.total_tokens:,} tokens in total"
    )
    if summary.fallbacks:
        print(f"  retrieval fell back to the whole schema: {', '.join(summary.fallbacks)}")
    print("  confidence → accuracy: " + "   ".join(
        f"{level}: {score}" for level, score in summary.by_confidence.items()
    ))  # fmt: skip
    print(f"  wall time: {_wall_text(wall, startup)}")
    if summary.not_scored:
        print()
        print(f"Not scored (LLM or database unavailable): {', '.join(summary.not_scored)}")
        rerun = f"{rerun_flags(sys.argv[1:])} --ids {' '.join(summary.not_scored)}"
        print(f"Rerun: uv run python -m evals.run {rerun}")
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Suite": f"{suite.name} ({suite.label})",
            "Questions": str(len(items)),
            "LLM": f"{settings.llm_provider} / {model}",
            "Code": _git_commit(),
            "Max rows / timeout": f"{shown.max_rows} / {shown.query_timeout_seconds} s",
            "Max SQL attempts": str(shown.max_sql_attempts),
            "Semantic layer": _layers(shown),
            "Schema retrieval": f"{shown.schema_retrieval} ({shown.retrieval_mode}, "
            f"top-k {shown.retrieval_top_k}, max {shown.retrieval_max_tables} tables)",
            "Few-shot examples": str(shown.few_shot_examples or "off"),
            "Pace": pace,
            "Wall time": _wall_text(wall, startup),
        }
        json_path = write_reports(
            args.report,
            outcomes,
            title=f"QueryMind eval: {suite.name} ({suite.label.split(',')[0]})",
            meta=meta,
            max_rows=shown.max_rows,
            run_flags=rerun_flags(sys.argv[1:]),
            reference_failures=reference_failures,
            tier_names=tier_names,
        )
        print(f"\nReport written to {args.report} and {json_path}")
    return 0


def rerun_flags(argv: Sequence[str]) -> str:
    """This run's flags minus the ones that pick questions or outputs (--ids, --limit, --report).

    A rerun of the *not scored* questions must use the same setup (provider, ablation arm,
    evidence...), or its answers would be mixed into the wrong arm of a comparison.
    """
    kept: list[str] = []
    dropping = False
    for arg in argv:
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            dropping = name in {"--ids", "--limit", "--report"}
            if dropping and "=" in arg:
                dropping = False  # --limit=5: the value came with it
                continue
        if not dropping:
            kept.append(arg)
    return " ".join(kept)


def _evaluate_with_ray(
    items: Sequence[GoldenQuestion],
    make_ask: PipelineAsk,
    references: dict[str, QueryResult],
    *,
    workers: int,
    limits: Limits,
    retries: int,
    backoff_seconds: float,
    on_outcome: Callable[[int, Outcome], None],
) -> tuple[list[Outcome], float]:
    """--workers N: the questions over N Ray workers. Also returns Ray's start-up time."""
    from evals.parallel import evaluate_parallel, ray_running  # Ray is optional (eval group)

    with ray_running(workers) as startup:
        outcomes = evaluate_parallel(
            items,
            make_ask=make_ask,
            references=references,
            workers=workers,
            limits=limits,
            retries=retries,
            backoff_seconds=backoff_seconds,
            on_outcome=on_outcome,
        )
    return outcomes, startup


def _limits(provider: str, *, rpm: int | None, tpm: int | None) -> Limits:
    """The provider's free-tier limits, with --rpm / --tpm taking precedence."""
    limits = FREE_TIER[provider]
    return replace(
        limits,
        requests_per_minute=rpm or limits.requests_per_minute,
        tokens_per_minute=tpm or limits.tokens_per_minute,
    )


def _limits_text(limits: Limits) -> str:
    counted = "input + output" if limits.counts_output else "input only"
    return (
        f"limiter {limits.requests_per_minute} RPM / {limits.tokens_per_minute:,} TPM "
        f"({counted}, {limits.headroom:.0%} used)"
    )


def _wall_text(seconds: float, startup: float) -> str:
    started = f" (+ {startup:.0f} s to start Ray)" if startup else ""
    return f"{seconds - startup:.0f} s{started}"


def _pipelines(
    items: Sequence[GoldenQuestion],
    settings_for: Callable[[GoldenQuestion], Settings],
    *,
    wrap_llm: Callable[[LLMProvider], LLMProvider] = lambda llm: llm,
) -> dict[str, Pipeline]:
    """One Pipeline per database the questions use (BIRD: up to 11), keyed by database name.

    They share one LLM client. Embeddings only where schema retrieval may run: BIRD shows
    the whole schema and has no index, so its pipelines get none.
    """
    first = settings_for(items[0])
    llm = wrap_llm(create_llm_provider(first))
    embeddings = create_embedding_provider(first)
    pipelines: dict[str, Pipeline] = {}
    for item in items:
        settings = settings_for(item)
        if settings.postgres_db not in pipelines:
            pipelines[settings.postgres_db] = Pipeline(
                llm=llm,
                settings=settings,
                embeddings=None if settings.schema_retrieval == "off" else embeddings,
            )
    return pipelines


def _print_references(
    items: Sequence[GoldenQuestion],
    references: dict[str, QueryResult],
    failures: dict[str, str],
) -> int:
    """--references-only: did every reference SQL run, and how big are the answers?"""
    truncated = sorted(qid for qid, result in references.items() if result.truncated)
    empty = sorted(qid for qid, result in references.items() if not result.rows)
    largest = max((r.row_count for r in references.values()), default=0)
    print(f"Reference SQL: {len(references)} of {len(items)} ran, {len(failures)} failed")
    print(f"  largest answer: {largest:,} rows; empty answers: {', '.join(empty) or 'none'}")
    if truncated:
        print(f"  cut off at the row cap (can't be compared fully): {', '.join(truncated)}")
    for qid, why in sorted(failures.items()):
        print(f"  FAILED {qid}: {why}")
    return 1 if failures or truncated else 0


def _layers(settings: Settings) -> str:
    return ", ".join(p.name for p in settings.semantic_layer_paths) or "off"


class AuditCapture(logging.Handler):
    """Collects what the logs say about the current question (reset it with `last = ...`).

    - The pipeline's audit line: the SQL tried and the number of queries. When a question fails
      (unsafe SQL, a database error), the pipeline raises, so the runner never sees the SQL; the
      audit line always records it, so the report can still show what QueryMind tried.
    - Every "llm call" line (llm/langchain_provider.py): one more call, and its tokens. Adding
      them up gives what one question cost, whichever nodes called the LLM (4.2).
    """

    LOGGERS = ("querymind.audit", "querymind.llm")

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.last = AuditedAsk()

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith("querymind.llm"):
            if record.getMessage() == "llm call":
                self.last = replace(
                    self.last,
                    llm_calls=self.last.llm_calls + 1,
                    input_tokens=self.last.input_tokens + (getattr(record, "input_tokens", 0) or 0),
                    output_tokens=self.last.output_tokens
                    + (getattr(record, "output_tokens", 0) or 0),
                )
            return
        self.last = replace(
            self.last,
            sql=getattr(record, "sql", None),
            sql_attempts=getattr(record, "sql_attempts", 0),
            schema_mode=getattr(record, "schema_mode", None),
            schema_tables=len(getattr(record, "schema_tables", None) or []),
            schema_tokens=getattr(record, "schema_tokens", None) or 0,
        )

    @classmethod
    def attach(cls) -> "AuditCapture":
        handler = cls()
        for name in cls.LOGGERS:
            source = logging.getLogger(name)
            source.addHandler(handler)
            source.setLevel(logging.INFO)  # INFO lines are dropped by default (WARNING and up)
        return handler


def run_references(
    items: Sequence[GoldenQuestion], settings_for: Callable[[GoldenQuestion], Settings]
) -> tuple[dict[str, QueryResult], dict[str, str]]:
    """Run every reference SQL once, through the same validator and executor as the agent.

    `settings_for` says which database (and row cap) each question uses. Returns the results,
    and the questions whose reference failed (id → why): those get no verdict instead of
    stopping the whole run, and the report lists them.
    """
    groups: dict[str, list[GoldenQuestion]] = defaultdict(list)
    for item in items:
        groups[settings_for(item).postgres_db].append(item)
    results: dict[str, QueryResult] = {}
    failures: dict[str, str] = {}
    for group in groups.values():
        settings = settings_for(group[0])
        with connect_readonly(settings) as conn:
            tables = introspect_schema(conn, sample_rows=0).table_names
            for item in group:
                try:
                    query = validate_sql(
                        item.sql, allowed_tables=tables, max_rows=settings.max_rows
                    )
                    results[item.id] = execute_query(
                        conn, query, timeout_seconds=settings.query_timeout_seconds
                    )
                except UnsafeSqlError as exc:
                    failures[item.id] = f"rejected by the validator: {exc.reason}"
                except QueryError as exc:
                    failures[item.id] = f"{type(exc).__name__}: {exc}"
    return results, failures


def _progress_line(number: int, total: int, outcome: Outcome) -> str:
    label = f"{status(outcome):5}"
    detail = "" if outcome.passed else f"  {outcome.error or ''} {outcome.reason}".rstrip()
    notes = []
    if outcome.sql_attempts > 1:
        notes.append(f"{outcome.sql_attempts} SQL tries")
    if outcome.provider_attempts > 1:
        notes.append(f"provider attempt {outcome.provider_attempts}")
    noted = f" ({', '.join(notes)})" if notes else ""
    return (
        f"[{number:2}/{total}] {label} {outcome.item.id:30} "
        f"{outcome.seconds:5.1f}s{noted}{detail[:120]}"
    )


def _git_commit() -> str:
    """The commit the code was at, plus '(uncommitted changes)' if the tree was dirty."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit} (uncommitted changes)" if dirty else commit


if __name__ == "__main__":
    # Run main() from the importable module evals.run, not from this copy named __main__: Ray
    # sends objects to its workers by module name (PipelineAsk...), and a worker can import
    # evals.run but its own __main__ is Ray's worker script (4.3).
    from evals.run import main as importable_main

    sys.exit(importable_main())
