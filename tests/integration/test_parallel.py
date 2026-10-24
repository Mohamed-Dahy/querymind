"""evals/parallel.py on a real local Ray: workers, the shared limiter, per-worker token counts.

Here and not in tests/unit because starting Ray takes ~10 s. Skipped when Ray isn't installed
(`uv sync --group eval`), e.g. in CI.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

ray = pytest.importorskip("ray")

from evals.golden_set import GoldenQuestion  # noqa: E402  (after the skip)
from evals.parallel import (  # noqa: E402
    LLMWrapper,
    evaluate_parallel,
    ray_running,
    rescore_parallel,
)
from evals.ratelimit import Limits  # noqa: E402
from evals.report import write_reports  # noqa: E402
from evals.rescore import main, rescore  # noqa: E402
from evals.run import AskFn  # noqa: E402
from querymind.agent.schemas import AgentReply  # noqa: E402
from querymind.config import Settings  # noqa: E402
from querymind.llm.fake import FakeLLMProvider  # noqa: E402
from tests.integration.test_rescore import answered, saved  # noqa: E402
from tests.unit.test_eval_run import answer, question, result  # noqa: E402

pytestmark = pytest.mark.integration

ITEMS = [question(f"q{n}") for n in range(1, 7)]
REFERENCES = {item.id: result([[int(item.id[1:])]]) for item in ITEMS}
LIMITS = Limits(requests_per_minute=600, tokens_per_minute=1_000_000, counts_output=True)


@pytest.fixture(scope="module", autouse=True)
def local_ray() -> Iterator[None]:
    """One Ray for the whole module; ray_running then reuses it."""
    with ray_running(workers=2):
        yield


def fake_asker(wrap: LLMWrapper) -> AskFn:
    """Runs inside a worker: answers qN with N (q4 wrongly), one booked "LLM call" each."""
    llm = wrap(FakeLLMProvider(["ok"] * len(ITEMS)))
    log = logging.getLogger("querymind.llm.fake")

    def ask(item: GoldenQuestion) -> AgentReply:
        llm.generate_text(f"{item.id}?")  # through RateLimitedLLM → the shared limiter actor
        log.info("llm call", extra={"input_tokens": 100, "output_tokens": 10})
        n = int(item.id[1:])
        return answer([[n + 1 if n == 4 else n]])

    return ask


def test_parallel_run_gives_the_same_outcomes_in_question_order() -> None:
    finished: list[int] = []
    outcomes = evaluate_parallel(
        ITEMS,
        make_ask=fake_asker,
        references=REFERENCES,
        workers=2,
        limits=LIMITS,
        on_outcome=lambda number, _outcome: finished.append(number),
    )
    assert [o.item.id for o in outcomes] == [item.id for item in ITEMS]
    assert [o.passed for o in outcomes] == [True, True, True, False, True, True]
    assert finished == [1, 2, 3, 4, 5, 6]
    # Each worker counts its own question's log lines: one call, 100 + 10 tokens, not more.
    assert {(o.llm_calls, o.input_tokens, o.output_tokens) for o in outcomes} == {(1, 100, 10)}


def test_parallel_rescore_matches_the_sequential_one(settings: Settings) -> None:
    run = saved(
        answered("SELECT count(*) AS n FROM customers", passed=False, ex=False),
        answered("SELECT count(*) FROM orders", passed=True, ex=True),
        answered("SELECT count(*), 1 FROM customers", passed=False, ex=False),
    )
    assert rescore_parallel(run.outcomes, settings, max_rows=1000, workers=2) == rescore(
        run, settings
    )


def test_rescore_command_with_workers(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = answered("SELECT count(*) FROM customers", passed=True, ex=True)
    json_path = write_reports(tmp_path / "run.md", [good], title="t", meta={}, max_rows=1000)
    assert main([str(json_path), "--workers", "2"]) == 0
    printed = capsys.readouterr().out
    assert "2 Ray workers" in printed
    assert "Changed verdicts: none" in printed
