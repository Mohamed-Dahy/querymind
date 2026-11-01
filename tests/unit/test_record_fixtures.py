"""Recording the web UI's fixtures (scripts/record_fixtures.py), with a fake pipeline. No LLM."""

import json
from collections.abc import Generator
from pathlib import Path

from fastapi.testclient import TestClient

from querymind.agent.events import AgentEvent, Finished, StepEvent
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
from scripts.record_fixtures import (
    RECORDINGS,
    Recorded,
    read_stream,
    record,
    select,
    write_fixtures,
)

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

    def stream(
        self, question: str, *, conversation_id: str | None = None
    ) -> Generator[AgentEvent | Finished]:
        self.conversation_ids[question] = conversation_id
        yield StepEvent(node="retrieve_schema", status="started")
        reply = self.replies[question]
        if isinstance(reply, Exception):
            raise reply
        yield Finished(reply)


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
    assert {name: item.reply["kind"] for name, item in replies.items()} == {
        "answer": "answer",
        "followup": "answer",
        "clarification": "clarification",
        "refusal": "refusal",
        "monthly": "answer",
        "single": "answer",
        "repair": "answer",
    }
    # The events before the reply are kept next to it.
    assert [event["type"] for event in replies["answer"].events] == ["step"]
    followup = next(r for r in RECORDINGS if r.name == "followup")
    # The follow-up was sent with the answer's conversation id, so it continues that talk.
    assert (
        pipeline.conversation_ids[followup.question] == replies["answer"].reply["conversation_id"]
    )


def test_a_reply_of_the_wrong_kind_is_a_problem_and_is_not_kept() -> None:
    # The LLM just answered the ambiguous question instead of asking back.
    with client_for(scripted(clarification=ANSWER)) as client:
        replies, problems = record(client)

    assert "clarification" not in replies
    assert problems == ["clarification: wanted kind 'clarification', got 'answer'"]


def test_an_api_error_is_a_problem_and_its_follow_up_is_skipped() -> None:
    with client_for(scripted(answer=UnsafeSqlError("Only SELECT queries are allowed."))) as client:
        replies, problems = record(client)

    assert set(replies) == {"clarification", "refusal", "monthly", "single", "repair"}
    assert problems == [
        "answer: unsafe_sql: Only SELECT queries are allowed.",
        "followup: skipped, 'answer' failed",
    ]


def test_write_fixtures_writes_utf8_json_with_lf(tmp_path: Path) -> None:
    reply = {"kind": "refusal", "message": "Ça ne change rien — I only read data."}
    events = [{"type": "step", "node": "generate_sql"}]

    [path, stream] = write_fixtures(
        {"refusal": Recorded(reply=reply, events=events)}, tmp_path / "recorded"
    )

    assert path == tmp_path / "recorded" / "refusal.json"
    assert stream == tmp_path / "recorded" / "streams" / "refusal.json"
    assert json.loads(stream.read_text(encoding="utf-8")) == events
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"}\n")
    assert json.loads(raw.decode("utf-8")) == reply
    assert "—" in raw.decode("utf-8")  # kept as is, not escaped to —


def test_read_stream_takes_the_data_lines_and_skips_pings() -> None:
    body = 'data: {"type":"step"}\n\n: ping\n\ndata: {"type":"final"}\n\n'

    assert read_stream(body) == [{"type": "step"}, {"type": "final"}]


def test_every_follow_up_continues_an_earlier_recording() -> None:
    names: set[str] = set()
    for recording in RECORDINGS:
        assert recording.follows is None or recording.follows in names
        names.add(recording.name)


def test_select_takes_the_named_recordings_in_order() -> None:
    assert [r.name for r in select(["single", "monthly"])] == ["monthly", "single"]


def test_select_adds_the_recording_a_follow_up_needs() -> None:
    # The follow-up needs the answer's conversation, so the answer is asked too (main() then
    # writes only the follow-up's file).
    assert [r.name for r in select(["followup"])] == ["answer", "followup"]


def test_recording_a_selection_asks_only_those_questions() -> None:
    pipeline = scripted()
    with client_for(pipeline) as client:
        replies, problems = record(client, select(["monthly", "single"]))

    assert problems == []
    assert set(replies) == {"monthly", "single"}
    assert set(pipeline.conversation_ids) == {
        r.question for r in RECORDINGS if r.name in {"monthly", "single"}
    }
