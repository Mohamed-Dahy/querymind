"""The eval runner with fakes: scoring, retries, errors and the report. No DB, no LLM."""

import logging
from collections.abc import Callable, Sequence

from evals.golden_set import GoldenQuestion
from evals.run import (
    AuditCapture,
    AuditedAsk,
    build_report,
    evaluate,
    evaluate_question,
    summarize_outcomes,
)
from querymind.agent.schemas import (
    AnswerSummary,
    AskResult,
    Clarification,
    ClarificationRequest,
    Confidence,
    SqlAttempt,
)
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError
from querymind.db.executor import QueryResult
from querymind.llm import LLMProviderError
from querymind.safety.errors import UnsafeSqlError

HIGH = Confidence(level="high", reasons=(), points=0)


def result(rows: Sequence[Sequence[object]]) -> QueryResult:
    return QueryResult.model_validate(
        {
            "columns": ["n"],
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "duration_ms": 1,
        }
    )


def question(qid: str, tier: str = "easy", tags: tuple[str, ...] = ()) -> GoldenQuestion:
    return GoldenQuestion.model_validate(
        {"id": qid, "tier": tier, "question": f"question {qid}?", "sql": "SELECT 1", "tags": tags}
    )


FIXED = SqlAttempt(
    sql="SELECT round(n, 2) FROM t LIMIT 1001",
    error_kind="db_error",
    message="The database could not run the query: function round(real, integer) does not exist",
    detail="function round(real, integer) does not exist",
)


def answer(rows: list[list[object]], *, retried: bool = False) -> AskResult:
    return AskResult(
        question="q",
        sql="SELECT n FROM t LIMIT 1001",
        explanation="",
        result=result(rows),
        answer=AnswerSummary(text="", from_llm=False),
        confidence=HIGH,
        failed_attempts=(FIXED,) if retried else (),
        sql_attempts=2 if retried else 1,
    )


REFERENCE = {"q1": result([[91]]), "q2": result([[408]]), "q3": result([[156]])}


def reference(item: GoldenQuestion) -> QueryResult:
    return REFERENCE[item.id]


def asker(replies: dict[str, list[object]]) -> Callable[[str], AskResult | ClarificationRequest]:
    """Answer each question with the next queued reply: rows, an AskResult, or an exception."""

    def ask(text: str) -> AskResult | ClarificationRequest:
        reply = replies[text.removeprefix("question ").removesuffix("?")].pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, AskResult | ClarificationRequest):
            return reply
        return answer(reply)  # type: ignore[arg-type]

    return ask


def no_sleep(_seconds: float) -> None:
    pass


def test_correct_and_wrong_answers() -> None:
    ask = asker({"q1": [[[91]]], "q2": [[[407]]]})
    right = evaluate_question(question("q1"), ask=ask, reference=reference)
    wrong = evaluate_question(question("q2"), ask=ask, reference=reference)
    assert (right.passed, right.strict, right.sql) == (True, True, "SELECT n FROM t LIMIT 1001")
    assert (wrong.passed, wrong.error) == (False, None)
    assert wrong.reason == "no column matches the values of 'n'"


def test_pipeline_errors_count_as_wrong() -> None:
    ask = asker({"q1": [UnsafeSqlError("Only SELECT queries are allowed.")]})
    outcome = evaluate_question(question("q1"), ask=ask, reference=reference)
    assert outcome.passed is False
    assert outcome.error == "UnsafeSqlError"
    assert outcome.reason == "Only SELECT queries are allowed."
    assert outcome.sql is None


def test_failed_questions_keep_the_sql_that_was_tried() -> None:
    ask = asker({"q1": [UnsafeSqlError("Only SELECT queries are allowed.")]})
    outcome = evaluate_question(
        question("q1"),
        ask=ask,
        reference=reference,
        last_audit=lambda: AuditedAsk(sql="DELETE FROM orders", sql_attempts=3),
    )
    assert (outcome.sql, outcome.sql_attempts) == ("DELETE FROM orders", 3)


def test_audit_capture_reads_the_sql_and_attempts() -> None:
    capture = AuditCapture.attach()
    try:
        logging.getLogger("querymind.audit").info(
            "ask", extra={"sql": "SELECT 1", "outcome": "x", "sql_attempts": 2}
        )
        assert capture.last == AuditedAsk(sql="SELECT 1", sql_attempts=2)
    finally:
        logging.getLogger("querymind.audit").removeHandler(capture)


def test_self_corrected_answers_are_counted() -> None:
    ask = asker({"q1": [answer([[91]], retried=True)], "q2": [[[408]]]})
    outcomes = evaluate([question("q1"), question("q2")], ask=ask, reference=reference)
    assert [o.sql_attempts for o in outcomes] == [2, 1]
    summary = summarize_outcomes(outcomes)
    assert summary.corrected == ["q1"]
    report = build_report(outcomes, title="t", meta={})
    assert "| Passed after self-correction (SQL retried) | 1 |" in report
    assert "| `q1` | easy | PASS | 2 | high |" in report  # SQL tries, then confidence


def test_rate_limits_are_retried_not_scored() -> None:
    pauses: list[float] = []
    ask = asker({"q1": [LLMProviderError("429 rate limited"), [[91]]]})
    outcome = evaluate_question(
        question("q1"), ask=ask, reference=reference, retries=2, backoff_seconds=10,
        sleep=pauses.append,
    )  # fmt: skip
    assert outcome.passed is True
    assert outcome.provider_attempts == 2
    assert pauses == [10]


def test_rate_limits_give_up_after_the_retries() -> None:
    errors: list[object] = [LLMProviderError("429")] * 3
    outcome = evaluate_question(
        question("q1"), ask=asker({"q1": errors}), reference=reference, retries=2, sleep=no_sleep
    )
    assert (outcome.passed, outcome.error, outcome.provider_attempts) == (
        False,
        "LLMProviderError",
        3,
    )
    assert outcome.scored is False  # no verdict: the agent never got to answer


def test_database_down_is_not_scored() -> None:
    errors: list[object] = [DatabaseUnavailableError("Could not connect to the database.")] * 3
    outcome = evaluate_question(
        question("q1"), ask=asker({"q1": errors}), reference=reference, retries=2, sleep=no_sleep
    )
    assert (outcome.scored, outcome.provider_attempts) == (False, 3)  # an outage, not wrong


def test_unscored_questions_are_left_out_of_the_accuracy() -> None:
    ask = asker({"q1": [[[91]]], "q2": [LLMProviderError("429")] * 3})
    outcomes = evaluate(
        [question("q1"), question("q2")], ask=ask, reference=reference, sleep=no_sleep
    )
    summary = summarize_outcomes(outcomes)
    assert str(summary.accuracy) == "1/1 (100.0%)"  # not 1/2
    assert summary.not_scored == ["q2"]
    assert summary.errors == {}  # a rate limit isn't an agent error
    report = build_report(outcomes, title="t", meta={})
    assert "| Not scored (LLM or database unavailable) | 1 |" in report
    assert "--ids q2" in report
    assert "| `q2` | easy | SKIP | - | - |" in report
    assert "## Failures in detail" not in report


def test_summary_scores_by_tier_and_tag() -> None:
    items = [question("q1", "easy", ("filter",)), question("q2", "medium", ("join",)),
             question("q3", "hard", ("join", "window"))]  # fmt: skip
    ask = asker({"q1": [[[91]]], "q2": [[[408]]], "q3": [QueryExecutionError("bad", detail="x")]})
    outcomes = evaluate(items, ask=ask, reference=reference, sleep=no_sleep)

    summary = summarize_outcomes(outcomes)
    assert str(summary.accuracy) == "2/3 (66.7%)"
    assert {tier: str(s) for tier, s in summary.by_tier.items()} == {
        "easy": "1/1 (100.0%)",
        "medium": "1/1 (100.0%)",
        "hard": "0/1 (0.0%)",
    }
    assert str(summary.by_tag["join"]) == "1/2 (50.0%)"
    assert summary.errors == {"QueryExecutionError": 1}


def test_progress_callback_and_delay() -> None:
    seen: list[tuple[int, str]] = []
    pauses: list[float] = []
    evaluate(
        [question("q1"), question("q2")],
        ask=asker({"q1": [[[91]]], "q2": [[[408]]]}),
        reference=reference,
        delay_seconds=1.5,
        on_outcome=lambda number, o: seen.append((number, o.item.id)),
        sleep=pauses.append,
    )
    assert seen == [(1, "q1"), (2, "q2")]
    assert pauses == [1.5]  # between questions only, not before the first


def test_report_shows_scores_and_failures() -> None:
    outcomes = evaluate(
        [question("q1"), question("q2")],
        ask=asker({"q1": [[[91]]], "q2": [[[999]]]}),
        reference=reference,
        sleep=no_sleep,
    )
    report = build_report(outcomes, title="Test eval", meta={"LLM": "fake"})
    assert report.startswith("# Test eval\n")
    assert "- **LLM:** fake" in report
    assert "| **Execution accuracy** (extra columns allowed) | **1/2 (50.0%)** |" in report
    assert "| `q2` | easy | FAIL | 1 | high |" in report
    assert "### `q2` (easy): wrong result" in report
    assert "```sql\nSELECT n FROM t LIMIT 1001\n```" in report  # QueryMind's SQL


def test_asking_back_on_a_golden_question_is_wrong() -> None:
    # Golden questions are clear, so a clarifying question there is a false alarm.
    asks = ClarificationRequest(
        question="q", clarification=Clarification(question="Which year?", options=["1997"])
    )
    outcome = evaluate_question(question("q1"), ask=asker({"q1": [asks]}), reference=reference)
    assert (outcome.passed, outcome.scored, outcome.error) == (False, True, None)
    assert outcome.reason == "asked back instead of answering: Which year?"


def test_calibration_counts_accuracy_per_confidence_level() -> None:
    low = Confidence(level="low", reasons=("x",), points=3)
    unsure = answer([[999]]).model_copy(update={"confidence": low})  # wrong AND unsure
    ask = asker({"q1": [[[91]]], "q2": [unsure], "q3": [UnsafeSqlError("no")]})
    outcomes = evaluate(
        [question("q1"), question("q2"), question("q3")], ask=ask, reference=reference
    )
    summary = summarize_outcomes(outcomes)
    assert {level: str(s) for level, s in summary.by_confidence.items()} == {
        "high": "1/1 (100.0%)",
        "medium": "0/0 (0.0%)",
        "low": "0/1 (0.0%)",  # q3 gave no answer, so it has no confidence and isn't counted
    }
    assert "| low | 0/1 (0.0%) |" in build_report(outcomes, title="t", meta={})
