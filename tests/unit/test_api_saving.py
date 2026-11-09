"""Saving turns from the API (U3.2): what is saved, when, and that a failed save loses no answer."""

import json
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.agent.events import AgentEvent, Finished, SqlEvent, StepEvent
from querymind.agent.schemas import AgentReply, Clarification, ClarificationRequest, Refusal
from querymind.api.main import create_app
from querymind.api.routes import ask_stream
from querymind.api.schemas import AskRequest, FinalEvent
from querymind.llm import LLMProviderError
from querymind.store.conversations import Owner
from querymind.store.errors import ConversationNotFoundError, StoreUnavailableError
from tests.auth_helpers import TEST_OWNER, TEST_USER, signed_in
from tests.unit.test_api import RESULT

STEP = StepEvent(node="generate_sql", status="started")
SQL = SqlEvent(attempt=1, sql="SELECT 1", explanation="One.")


class FakePipeline:
    def __init__(self, reply: AgentReply = RESULT, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error

    def ask(self, question: str, *, conversation_id: str | None = None) -> AgentReply:
        if self.error is not None:
            raise self.error
        return self.reply

    def stream(
        self, question: str, *, conversation_id: str | None = None
    ) -> Generator[AgentEvent | Finished]:
        yield STEP
        yield SQL
        if self.error is not None:
            raise self.error
        yield Finished(self.reply)


class FakeStore:
    """Remembers what it was asked to save, or fails."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.saved: list[dict[str, Any]] = []
        self.foreign: set[UUID] = set()  # conversations that belong to someone else

    def check_owner(self, conversation_id: UUID, *, owner: Owner) -> None:
        if conversation_id in self.foreign:
            raise ConversationNotFoundError("This conversation doesn't exist.")

    def save_turn(self, conversation_id: UUID, **turn: Any) -> int:
        if self.error is not None:
            raise self.error
        self.saved.append({"conversation_id": conversation_id, **turn})
        return len(self.saved)


def client(pipeline: FakePipeline, store: FakeStore | None) -> TestClient:
    app = signed_in(create_app(pipeline=pipeline, store=store))  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def stream_events(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_post_ask_saves_the_turn() -> None:
    store = FakeStore()

    with client(FakePipeline(), store) as api:
        body = api.post("/ask", json={"question": "How many customers are there?"}).json()

    [saved] = store.saved
    assert str(saved["conversation_id"]) == body["conversation_id"]
    assert saved["question"] == "How many customers are there?"
    assert saved["kind"] == "answer"
    assert saved["sql"] == body["sql"]
    assert saved["reply"] == body
    assert list(saved["trace"]) == []  # no steps to keep on this path


def test_the_stream_saves_the_turn_with_its_steps() -> None:
    store = FakeStore()

    with client(FakePipeline(), store) as api:
        events = stream_events(api.post("/ask/stream", json={"question": "q"}))

    [saved] = store.saved
    assert saved["reply"] == events[-1]["reply"]
    assert [event["type"] for event in saved["trace"]] == ["step", "sql"]
    assert saved["trace"][1]["sql"] == "SELECT 1"


def test_the_stream_has_saved_before_it_sends_the_answer() -> None:
    # So a reload right after the answer finds it.
    store = FakeStore()
    events = ask_stream(AskRequest(question="q"), TEST_USER, FakePipeline(), [], store, None)  # type: ignore[arg-type]

    for event in events:
        if isinstance(event, FinalEvent):
            break
    else:
        pytest.fail("no final event")

    assert len(store.saved) == 1


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        (
            ClarificationRequest(
                question="q",
                clarification=Clarification(question="By what?", options=["a", "b"]),
            ),
            "clarification",
        ),
        (Refusal(question="q", message="No."), "refusal"),
    ],
)
def test_a_question_back_or_a_refusal_is_saved_without_sql(reply: AgentReply, kind: str) -> None:
    store = FakeStore()

    with client(FakePipeline(reply), store) as api:
        api.post("/ask/stream", json={"question": "q"})

    assert store.saved[0]["kind"] == kind
    assert store.saved[0]["sql"] is None


def test_a_follow_up_is_saved_in_its_conversation() -> None:
    store = FakeStore()
    conversation_id = uuid4()

    with client(FakePipeline(), store) as api:
        api.post("/ask", json={"question": "q", "conversation_id": str(conversation_id)})

    assert store.saved[0]["conversation_id"] == conversation_id


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_a_failed_question_isnt_saved(path: str) -> None:
    store = FakeStore()

    with client(FakePipeline(error=LLMProviderError("down")), store) as api:
        api.post(path, json={"question": "q"})

    assert store.saved == []


def test_a_stopped_run_isnt_saved() -> None:
    store = FakeStore()
    events = ask_stream(AskRequest(question="q"), TEST_USER, FakePipeline(), [], store, None)  # type: ignore[arg-type]

    next(events)
    events.close()

    assert store.saved == []


def test_a_failing_store_doesnt_lose_the_answer_on_post_ask() -> None:
    store = FakeStore(error=StoreUnavailableError("down"))

    with client(FakePipeline(), store) as api:
        response = api.post("/ask", json={"question": "q"})

    assert response.status_code == 200
    assert response.json()["answer"] == "There are 91 customers."


def test_a_failing_store_doesnt_lose_the_answer_on_the_stream() -> None:
    store = FakeStore(error=StoreUnavailableError("down"))

    with client(FakePipeline(), store) as api:
        events = stream_events(api.post("/ask/stream", json={"question": "q"}))

    assert events[-1]["type"] == "final"


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_with_history_off_nothing_changes(path: str) -> None:
    with client(FakePipeline(), None) as api:
        response = api.post(path, json={"question": "q"})

    assert response.status_code == 200


# --- Whose conversation (5.1c) ---


def test_a_turn_is_saved_for_the_logged_in_user() -> None:
    store = FakeStore()

    with client(FakePipeline(), store) as api:
        api.post("/ask", json={"question": "q"})

    assert store.saved[0]["owner"] == TEST_OWNER


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_someone_elses_conversation_cant_be_continued(path: str) -> None:
    # The agent would load its turns as memory: refused before the pipeline runs.
    theirs = uuid4()
    store = FakeStore()
    store.foreign.add(theirs)
    pipeline = FakePipeline(error=AssertionError("the pipeline must not run"))

    with client(pipeline, store) as api:
        response = api.post(path, json={"question": "And by year?", "conversation_id": str(theirs)})

    if path == "/ask":
        assert response.status_code == 404
        body = response.json()
    else:
        [event] = stream_events(response)  # nothing ran: no step events, only the error
        assert event["type"] == "error"
        body = event["error"]
    assert body["error"] == "conversation_not_found"
    assert store.saved == []
