"""The clarification suite: its YAML is well-formed and balanced, and replies are classified."""

from pathlib import Path

import pytest

from evals.clarify import ClarifyCase, evaluate_case, load_clarify_set, score_line
from evals.golden_set import GoldenSetError
from querymind.agent.schemas import (
    AgentReply,
    AnswerSummary,
    AskResult,
    Clarification,
    ClarificationRequest,
    Refusal,
)
from querymind.db.errors import QueryExecutionError
from querymind.db.executor import QueryResult
from querymind.llm import LLMProviderError

SUITE = load_clarify_set()
CASE = ClarifyCase(id="c01", question="Who are our best customers?", expect="clarify", why="x")


def answer() -> AskResult:
    return AskResult(
        question="q",
        sql="SELECT 1 LIMIT 1001",
        explanation="",
        result=QueryResult(columns=["n"], rows=[[1]], row_count=1, truncated=False, duration_ms=1),
        answer=AnswerSummary(text="1", from_llm=False),
    )


ASKS = ClarificationRequest(
    question="q", clarification=Clarification(question="Best by what?", options=["a", "b"])
)


def test_the_suite_has_5_ambiguous_5_clear_and_2_change_requests() -> None:
    expects = [case.expect for case in SUITE.cases]
    assert (expects.count("clarify"), expects.count("answer"), expects.count("refuse")) == (5, 5, 2)


def test_broken_files_fail_loudly(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text(
        "database: x\nversion: 1\ncases:\n"
        "  - {id: c1, question: q, expect: maybe, why: w}\n",  # "maybe" isn't a kind
        encoding="utf-8",
    )
    with pytest.raises(GoldenSetError, match=r"bad\.yaml"):
        load_clarify_set(path)


@pytest.mark.parametrize(
    ("reply", "kind"),
    [(ASKS, "clarify"), (Refusal(question="q", message="I only read data."), "refuse")],
)
def test_reply_kinds(reply: AgentReply, kind: str) -> None:
    assert evaluate_case(CASE, lambda _q: reply).got == kind


def test_an_answer_is_an_answer() -> None:
    outcome = evaluate_case(CASE, lambda _q: answer())
    assert (outcome.got, outcome.passed) == ("answer", False)  # it should have asked back


def test_failed_sql_still_counts_as_answering() -> None:
    # It didn't ask back: it tried SQL, which failed. For this suite that's an "answer".
    def fails(_question: str) -> AgentReply:
        raise QueryExecutionError("The database could not run the query: x", detail="x")

    assert evaluate_case(CASE, fails).got == "answer"


def test_provider_outage_gives_no_verdict() -> None:
    def down(_question: str) -> AgentReply:
        raise LLMProviderError("429")

    outcome = evaluate_case(CASE, down)
    assert outcome.got is None
    assert score_line([outcome], "clarify") == "asked back on ambiguous questions: 0/0"


def test_score_line_counts_by_expected_kind() -> None:
    outcomes = [evaluate_case(CASE, lambda _q: ASKS), evaluate_case(CASE, lambda _q: answer())]
    assert score_line(outcomes, "clarify") == "asked back on ambiguous questions: 1/2"
