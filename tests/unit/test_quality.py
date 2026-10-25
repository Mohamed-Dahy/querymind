"""The answer-quality eval (4.5) with fakes: cases, judging rules, resume, the report. No LLM.

These parts don't import deepeval or ragas, so they run in CI too (test_judges.py needs them).
"""

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from evals.golden_set import GoldenQuestion
from evals.quality import (
    METRIC_NAMES,
    AnswerCase,
    Judged,
    QualityRun,
    RetrievalCase,
    SummaryCapture,
    Verdict,
    answer_case,
    build_report,
    collect_answers,
    judge_answers,
    judge_case,
    judge_context,
    load_run,
    retrieval_case,
    sample_ids,
    save_run,
)
from evals.report import Outcome
from evals.retrieval import Retrieved
from evals.run import AuditCapture, AuditedAsk
from querymind.agent.schemas import (
    AnswerSummary,
    AskResult,
    Clarification,
    ClarificationRequest,
    Confidence,
)
from querymind.agent.summarizer import SUMMARY_ROWS, summarize
from querymind.db.executor import QueryResult
from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.retrieval import SchemaSelection

HIGH = Confidence(level="high", reasons=(), points=0)
ITEM = GoldenQuestion.model_validate(
    {"id": "e01", "tier": "easy", "question": "How many customers?", "sql": "SELECT 1"}
)


def result(rows: list[list[object]], columns: tuple[str, ...] = ("n",)) -> QueryResult:
    return QueryResult.model_validate(
        {
            "columns": list(columns),
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "duration_ms": 1,
        }
    )


def reply(text: str, *, from_llm: bool, rows: list[list[object]] | None = None) -> AskResult:
    return AskResult(
        question=ITEM.question,
        sql="SELECT count(*) AS n FROM customers LIMIT 1001",
        explanation="",
        result=result(rows if rows is not None else [[91]]),
        answer=AnswerSummary(text=text, from_llm=from_llm),
        confidence=HIGH,
    )


def outcome(*, passed: bool = True, scored: bool = True, reason: str = "") -> Outcome:
    return Outcome(
        item=ITEM,
        passed=passed,
        strict=passed,
        reason=reason,
        error=None,
        sql="SELECT 1",
        seconds=0.1,
        provider_attempts=1,
        sql_attempts=1,
        confidence="high",
        scored=scored,
    )


def no_sleep(_seconds: float) -> None:
    pass


# ---------------------------------------------------------------- stage 1: what is saved


def test_shown_summary_is_judged_with_the_data_the_summarizer_saw() -> None:
    case = answer_case(ITEM, outcome(), reply("There are 91 customers.", from_llm=True), None)
    assert case.summary == "There are 91 customers." and case.from_llm and not case.skipped
    assert case.sql_match is True and case.ungrounded == []
    assert case.context == '{"columns": ["n"], "rows": [[91]]}\nAll 1 rows are shown.'


def test_the_context_has_only_the_rows_the_llm_was_shown() -> None:
    rows: list[list[object]] = [[i] for i in range(SUMMARY_ROWS + 5)]
    case = answer_case(ITEM, outcome(), reply("x", from_llm=True, rows=rows), None)
    assert case.context is not None
    assert case.context.endswith(f"Showing the first {SUMMARY_ROWS} of {SUMMARY_ROWS + 5} rows.")
    assert f"[{SUMMARY_ROWS}]" not in case.context  # row number 21 was never sent


def test_a_summary_grounding_rejected_is_judged_as_the_llm_wrote_it() -> None:
    fallback = reply("n: 91", from_llm=False)
    case = answer_case(ITEM, outcome(), fallback, ("There are 165 customers.", ["165"]))
    assert case.summary == "There are 165 customers." and not case.from_llm
    assert case.ungrounded == ["165"]


def test_nothing_to_judge_is_skipped_with_the_reason() -> None:
    no_llm = answer_case(
        ITEM, outcome(), reply("No data matched the question.", from_llm=False, rows=[]), None
    )
    assert no_llm.summary is None and "no LLM summary" in no_llm.skipped
    asked_back = ClarificationRequest(
        question=ITEM.question,
        clarification=Clarification(question="Which year?", options=["in 1997", "in 1998"]),
    )
    case = answer_case(ITEM, outcome(passed=False, reason="asked back"), asked_back, None)
    assert case.summary is None and case.skipped == "asked back" and case.sql_match is False


def test_an_outage_is_not_scored_and_asked_again_later() -> None:
    case = answer_case(ITEM, outcome(passed=False, scored=False, reason="429"), None, None)
    assert case.sql_match is None and case.skipped == "429"


@pytest.fixture
def summaries() -> Iterator[SummaryCapture]:
    capture = SummaryCapture.attach()
    yield capture
    logging.getLogger(SummaryCapture.LOGGER).removeHandler(capture)


def test_capture_gets_the_sentence_the_real_summarizer_rejected(
    summaries: SummaryCapture,
) -> None:
    summary = summarize(
        FakeLLMProvider(["There are 165 customers."]), question="How many?", result=result([[91]])
    )
    assert not summary.from_llm  # the user saw the fallback...
    assert summaries.rejected == ("There are 165 customers.", ["165"])  # ...the judge won't


def test_collect_answers_asks_scores_and_saves_each_question(
    summaries: SummaryCapture,
) -> None:
    saved: list[AnswerCase] = []
    collect_answers(
        [ITEM],
        ask=lambda _item: reply("There are 91 customers.", from_llm=True),
        reference=lambda _item: result([[91]]),
        capture=AuditCapture(),
        summaries=summaries,
        on_case=saved.append,
        retries=0,
        backoff_seconds=0,
    )
    assert [(c.id, c.sql_match, c.summary) for c in saved] == [
        ("e01", True, "There are 91 customers.")
    ]


# ---------------------------------------------------------------- stage 2: judging rules

CASE = AnswerCase(
    id="e01", question="How many customers?", summary="There are 91.", context="[[91]]"
)


def test_judge_case_scores_every_metric() -> None:
    verdicts = judge_case(
        CASE, judge=lambda _m, _c: (0.9, True, "ok"), retries=0, backoff_seconds=0
    )
    assert [(v.metric, v.score, v.passed) for v in verdicts] == [
        ("answer_relevancy", 0.9, True),
        ("faithfulness", 0.9, True),
        ("hallucination", 0.9, True),
    ]


def test_a_rate_limit_is_retried_then_not_scored_never_zero() -> None:
    calls: list[str] = []

    def judge(metric: str, _case: AnswerCase) -> tuple[float, bool, str]:
        calls.append(metric)
        if metric == "faithfulness":
            raise LLMProviderError("429")
        if metric == "hallucination":
            raise LLMOutputError("not the schema")
        return 1.0, True, ""

    verdicts = judge_case(CASE, judge=judge, retries=2, backoff_seconds=1, sleep=no_sleep)
    assert calls.count("faithfulness") == 3  # 1 + 2 retries
    faithfulness, hallucination = verdicts[1], verdicts[2]
    assert faithfulness.score is None and faithfulness.error == "LLMProviderError"
    assert hallucination.score is None and hallucination.error == "LLMOutputError"


def test_judge_answers_resumes_and_stops_when_the_judge_is_down() -> None:
    cases = [
        CASE,
        CASE.model_copy(update={"id": "e02"}),
        CASE.model_copy(update={"id": "e03"}),
        CASE.model_copy(update={"id": "e04", "summary": None, "skipped": "no answer"}),
    ]
    complete = Judged(
        id="e01", verdicts=[Verdict(metric=m, score=1.0) for m in METRIC_NAMES], calls=7
    )
    judged: list[Judged] = []

    def judge(_metric: str, case: AnswerCase) -> tuple[float, bool, str]:
        if case.id == "e03":
            raise LLMProviderError("daily limit")
        return 1.0, True, ""

    finished = judge_answers(
        cases,
        {"e01": complete},
        judge=judge,
        capture=AuditCapture(),
        on_judged=judged.append,
        retries=0,
        backoff_seconds=0,
    )
    assert not finished
    assert [j.id for j in judged] == ["e02", "e03"]  # e01 was done, stopped after e03, e04 skipped


def test_a_rerun_judges_only_the_metrics_without_a_score() -> None:
    saved = Judged(
        id="e01",
        verdicts=[
            Verdict(metric="answer_relevancy", score=0.9, passed=True),
            Verdict(metric="faithfulness", error="LLMProviderError"),  # not scored last time
        ],  # hallucination: dropped by --rejudge
        calls=5,
    )
    asked: list[str] = []
    judged: list[Judged] = []

    def judge(metric: str, _case: AnswerCase) -> tuple[float, bool, str]:
        asked.append(metric)
        return 1.0, True, ""

    judge_answers(
        [CASE],
        {"e01": saved},
        judge=judge,
        capture=AuditCapture(),
        on_judged=judged.append,
        retries=0,
        backoff_seconds=0,
    )
    assert asked == ["faithfulness", "hallucination"]  # answer_relevancy's 0.9 is kept
    assert [(v.metric, v.score) for v in judged[0].verdicts] == [
        ("answer_relevancy", 0.9),
        ("faithfulness", 1.0),
        ("hallucination", 1.0),
    ]
    assert judged[0].calls == 5  # earlier calls still count (this fake made no logged calls)


def test_the_judge_sees_the_question_with_the_data() -> None:
    # "408 orders were placed in 1997": 1997 is in the question, not in the rows.
    case = CASE.model_copy(update={"question": "How many orders in 1997?", "context": "[[408]]"})
    assert judge_context(case) == "Question: How many orders in 1997?\n\n[[408]]"


# ---------------------------------------------------------------- stage 3: retrieval


def test_retrieval_case_scores_tables_in_rank_order() -> None:
    selection = SchemaSelection(
        mode="retrieved",
        tables=("orders", "employees", "customers"),
        schema_tokens=0,
    )
    retrieved = Retrieved(ITEM, frozenset({"customers", "orders"}), selection, 0.0)
    case = retrieval_case(retrieved, lambda r, n: (len(set(r) & set(n)) / len(r), 1.0))
    assert case.retrieved == ["orders", "employees", "customers"]
    assert case.needed == ["customers", "orders"]
    assert case.id_precision == pytest.approx(2 / 3)


def test_sample_is_spread_out_and_the_same_every_time() -> None:
    ids = [f"q{i}" for i in range(58)]
    assert sample_ids(ids, 10) == [
        "q0",
        "q5",
        "q10",
        "q15",
        "q20",
        "q25",
        "q30",
        "q35",
        "q40",
        "q45",
    ]
    assert sample_ids(ids, 0) == []
    assert sample_ids(ids[:3], 10) == ids[:3]


# ---------------------------------------------------------------- report and JSON


def run_with_results() -> QualityRun:
    rejected = CASE.model_copy(
        update={"id": "m03", "summary": "Up 12%.", "ungrounded": ["12%"], "sql_match": True}
    )
    shown = CASE.model_copy(update={"from_llm": True, "sql_match": False})
    return QualityRun(
        meta={"Judge LLM": "gemini / gemini-3.5-flash"},
        answers=[shown, rejected],
        judged=[
            Judged(
                id="e01",
                verdicts=[
                    Verdict(metric="answer_relevancy", score=1.0, passed=True),
                    Verdict(metric="faithfulness", score=1.0, passed=True),
                    Verdict(metric="hallucination", error="LLMProviderError"),
                ],
                calls=7,
            ),
            Judged(
                id="m03",
                verdicts=[
                    Verdict(
                        metric="faithfulness",
                        score=1.0,
                        passed=True,
                        reason="12% follows from the two totals",
                    )
                ],
                calls=4,
            ),
        ],
        retrieval=[
            RetrievalCase(
                id="e01",
                question="q",
                needed=["customers"],
                retrieved=["customers", "orders"],
                id_precision=0.5,
                id_recall=1.0,
                llm_precision=1.0,
            ),
            RetrievalCase(
                id="h04",
                question="q",
                needed=["invoice", "track"],
                retrieved=["invoice"],
                id_precision=1.0,
                id_recall=0.5,
            ),
        ],
    )


def test_report_shows_scores_disagreements_and_misses() -> None:
    text = build_report(run_with_results())
    assert (
        "| faithfulness | is every claim supported by the data it was given? | 1.00 | 2/2 | 0 |"
        in text
    )
    assert "| hallucination | does it contradict that data? | - | 0/0 | 2 |" in text
    assert "They agree on 1 of 2 judged answers." in text  # m03: grounding no, judge yes
    assert "| `m03` | rejected (12% not in the data) | 1.00 | 12% follows" in text
    assert "**Wrong SQL, judged answer.**" in text  # e01's rows didn't match
    assert "| ID-based context recall | 0.750 | 2 (1 with every table) |" in text
    assert "| LLM context precision (judge) | 1.000 | 1 (sample) |" in text
    assert "`h04` (track)" in text
    assert "Judge cost: 11 calls (5.5 per answer)" in text


def test_saved_run_round_trips_and_rebuilds_the_report(tmp_path: Path) -> None:
    report = tmp_path / "quality.md"
    run = run_with_results()
    save_run(run, report)
    assert load_run(report.with_suffix(".json")) == run
    assert report.read_text(encoding="utf-8") == build_report(run)
    assert load_run(tmp_path / "missing.json") == QualityRun()


def test_audit_capture_counts_the_judges_calls() -> None:
    capture = AuditCapture()
    capture.last = AuditedAsk()
    record = logging.LogRecord("querymind.llm.x", logging.INFO, "", 0, "llm call", None, None)
    record.input_tokens, record.output_tokens = 100, 20
    capture.emit(record)
    assert (capture.last.llm_calls, capture.last.input_tokens) == (1, 100)
