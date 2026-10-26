"""Recording the web UI's fixtures (scripts/record_fixtures.py), with a fake pipeline. No LLM."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from querymind.agent.schemas import (
    AgentReply,
    AnswerSummary,
    AskResult,
    Clarification,
    ClarificationRequest,
    Confidence,
    Refusal,
)
from querymind.api.main import create_app
from querymind.db.executor import QueryResult
from querymind.safety.errors import UnsafeSqlError
from scripts.record_fixtures import RECORDINGS, record, write_fixtures

ANSWER = AskResult(
    question="What is the total sales amount per category?",
    sql="SELECT 1",
    explanation="Adds up sales per category.",
    result=QueryResult(columns=["total"], rows=[[1]], row_count=1, truncated=False, duration_ms=1),
    answer=AnswerSummary(text="Beverages sold the most.", from_llm=True),
    confidence=Confidence(level="high", reasons=(), points=0),
)
CLARIFY = ClarificationRequest(
    question="Which products are the most popular?",
    clarification=Clarification(question="Popular by what?", options=["units", "revenue"]),
)
REFUSE = Refusal(question="Delete all orders from 1996.", message="I can only read data.")


class ScriptedPipeline:
    """Answers each question with its scripted reply (or raises it), noting the conversation ids."""

    def __init__(self, replies: dict[str, AgentReply | Exception]) -> None:
        self.replies = replies
        self.conversation_ids: dict[str, str | None] = {}

    def ask(self, question: str, *, conversation_id: str | None = None) -> AgentReply:
        self.conversation_ids[question] = conversation_id
        reply = self.replies[question]
        if isinstance(reply, Exception):
            raise reply
        return reply


def client_for(pipeline: ScriptedPipeline) -> TestClient:
    return TestClient(create_app(pipeline=pipeline), raise_server_exceptions=False)


def scripted(**overrides: AgentReply | Exception) -> ScriptedPipeline:
    """The right reply for every recording, with some replaced by name."""
    right: dict[str, AgentReply] = {"clarification": CLARIFY, "refusal": REFUSE}
    replies = {r.question: overrides.get(r.name, right.get(r.name, ANSWER)) for r in RECORDINGS}
    return ScriptedPipeline(replies)


def test_records_every_reply_and_the_follow_up_continues_the_answer() -> None:
    pipeline = scripted()
    with client_for(pipeline) as client:
        replies, problems = record(client)

    assert problems == []
    assert {name: reply["kind"] for name, reply in replies.items()} == {
        "answer": "answer",
        "followup": "answer",
        "clarification": "clarification",
        "refusal": "refusal",
    }
    followup = next(r for r in RECORDINGS if r.name == "followup")
    # The follow-up was sent with the answer's conversation id, so it continues that talk.
    assert pipeline.conversation_ids[followup.question] == replies["answer"]["conversation_id"]


def test_a_reply_of_the_wrong_kind_is_a_problem_and_is_not_kept() -> None:
    # The LLM just answered the ambiguous question instead of asking back.
    with client_for(scripted(clarification=ANSWER)) as client:
        replies, problems = record(client)

    assert "clarification" not in replies
    assert problems == ["clarification: wanted kind 'clarification', got 'answer'"]


def test_an_api_error_is_a_problem_and_its_follow_up_is_skipped() -> None:
    with client_for(scripted(answer=UnsafeSqlError("Only SELECT queries are allowed."))) as client:
        replies, problems = record(client)

    assert set(replies) == {"clarification", "refusal"}
    assert problems == [
        "answer: HTTP 422 unsafe_sql: Only SELECT queries are allowed.",
        "followup: skipped, 'answer' failed",
    ]


def test_write_fixtures_writes_utf8_json_with_lf(tmp_path: Path) -> None:
    reply = {"kind": "refusal", "message": "Ça ne change rien — I only read data."}

    [path] = write_fixtures({"refusal": reply}, tmp_path / "recorded")

    assert path == tmp_path / "recorded" / "refusal.json"
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"}\n")
    assert json.loads(raw.decode("utf-8")) == reply
    assert "—" in raw.decode("utf-8")  # kept as is, not escaped to —


def test_every_follow_up_continues_an_earlier_recording() -> None:
    names: set[str] = set()
    for recording in RECORDINGS:
        assert recording.follows is None or recording.follows in names
        names.add(recording.name)
