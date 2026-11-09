"""POST /ask/stream (U2.2): events reach the client in order, end in `final` or `error`, say nothing
private, and a client going away reaches the pipeline. Fake or scripted LLM; no DB, no network.
"""

import json
import threading
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from querymind.agent import graph as graph_module
from querymind.agent import pipeline as pipeline_module
from querymind.agent.events import AgentEvent, Finished, StepEvent
from querymind.agent.schemas import AgentReply, Clarification, ClarificationRequest, Refusal
from querymind.api.main import create_app
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.db.schema import describe_schema
from querymind.llm import LLMOutputError, LLMProvider, LLMProviderError, LLMRateLimitError
from querymind.safety.errors import UnsafeSqlError
from tests.auth_helpers import signed_in
from tests.unit.test_api import RESULT
from tests.unit.test_conversation import IN_1997, SCHEMA, TOP, StubExecutor, pipeline_with

STARTED = StepEvent(node="retrieve_schema", status="started")
PRIVATE_DETAIL = "PRIVATE-DETAIL relation at line 7"


class FakeStreamingPipeline:
    """Yields one step, then the reply (or raises `error`). Notes what happened to it."""

    def __init__(self, reply: AgentReply = RESULT, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.conversation_ids: list[str | None] = []
        self.closed = False

    def ask(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> AgentReply:
        return self.reply

    def stream(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> Generator[AgentEvent | Finished]:
        self.conversation_ids.append(conversation_id)
        try:
            yield STARTED
            if self.error is not None:
                raise self.error
            yield Finished(self.reply)
        except GeneratorExit:
            self.closed = True
            raise


def post(pipeline: Any, body: dict[str, Any] | None = None) -> Any:
    """The streamed response, once it has ended. Failures come back as a real server's would."""
    with TestClient(
        signed_in(create_app(pipeline=pipeline)), raise_server_exceptions=False
    ) as client:
        return client.post("/ask/stream", json=body or {"question": "How many customers?"})


def events_of(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_it_streams_server_sent_events_that_a_proxy_must_not_buffer() -> None:
    response = post(FakeStreamingPipeline())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["cache-control"] == "no-cache"


def test_the_final_event_is_exactly_what_post_ask_replies() -> None:
    pipeline = FakeStreamingPipeline()
    conversation_id = str(uuid4())
    body = {"question": "How many customers?", "conversation_id": conversation_id}

    with TestClient(
        signed_in(create_app(pipeline=pipeline)), raise_server_exceptions=False
    ) as client:
        plain = client.post("/ask", json=body).json()
        events = events_of(client.post("/ask/stream", json=body))

    assert [event["type"] for event in events] == ["step", "final"]
    assert events[0] == {
        "type": "step",
        "node": "retrieve_schema",
        "status": "started",
        "duration_ms": None,
    }
    assert events[-1]["reply"] == plain


@pytest.mark.parametrize(
    ("reply", "kind"),
    [
        (
            ClarificationRequest(
                question="Best customers?",
                clarification=Clarification(question="By what?", options=["orders", "revenue"]),
            ),
            "clarification",
        ),
        (Refusal(question="Delete it.", message="I can only read data."), "refusal"),
    ],
)
def test_a_question_back_or_a_refusal_also_ends_in_final(reply: AgentReply, kind: str) -> None:
    last = events_of(post(FakeStreamingPipeline(reply)))[-1]

    assert last["type"] == "final"
    assert last["reply"]["kind"] == kind


def test_it_keeps_a_conversation_id_and_makes_one_when_there_is_none() -> None:
    known = str(uuid4())
    kept = FakeStreamingPipeline()
    made = FakeStreamingPipeline()

    assert (
        events_of(post(kept, {"question": "q", "conversation_id": known}))[-1]["reply"][
            "conversation_id"
        ]
        == known
    )
    reply = events_of(post(made, {"question": "q"}))[-1]["reply"]

    assert kept.conversation_ids == [known]
    assert made.conversation_ids == [reply["conversation_id"]]


@pytest.mark.parametrize(
    ("error", "code", "message"),
    [
        (UnsafeSqlError("Only SELECT queries are allowed."), "unsafe_sql",
         "Only SELECT queries are allowed."),
        (QueryTimeoutError("The query took longer than 10 s and was stopped."), "query_timeout",
         "The query took longer than 10 s and was stopped."),
        (QueryExecutionError("The database could not run the query: x", detail="x"), "query_failed",
         "The database could not run the query: x"),
        (DatabaseUnavailableError("Could not connect to the database."), "db_unavailable",
         "Could not connect to the database."),
        (LLMProviderError("groq request failed: boom"), "llm_unavailable",
         "The AI provider did not respond (network or outage)."),
        (LLMOutputError("groq output did not match"), "llm_bad_output",
         "The AI model declined or answered in the wrong format. Try rewording it."),
        (LLMRateLimitError("groq rate limit: Limit 100000"), "llm_rate_limited",
         "The AI provider's usage limit was reached. Please try again later."),
    ],
)  # fmt: skip
def test_an_error_in_the_run_has_the_code_and_message_of_post_ask(
    error: Exception, code: str, message: str
) -> None:
    streamed = post(FakeStreamingPipeline(error=error))

    # The events so far are kept, and the status was already sent.
    assert streamed.status_code == 200
    events = events_of(streamed)
    assert [event["type"] for event in events] == ["step", "error"]
    assert events[-1]["error"] == {"error": code, "message": message, "retry_after_seconds": None}


def test_a_rate_limit_in_the_stream_says_how_long_to_wait() -> None:
    error = LLMRateLimitError("groq rate limit", retry_after_seconds=17.2)

    last = events_of(post(FakeStreamingPipeline(error=error)))[-1]

    assert last["error"]["retry_after_seconds"] == 18


def test_a_bug_never_shows_its_details() -> None:
    secret = "password=hunter2 at /srv/app/db.py line 42"

    response = post(FakeStreamingPipeline(error=KeyError(secret)))

    assert events_of(response)[-1]["error"]["error"] == "internal_error"
    assert "hunter2" not in response.text


def test_a_bad_request_is_still_a_plain_422() -> None:
    response = post(FakeStreamingPipeline(), {"question": "   "})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


class EndlessPipeline:
    """Reports forever until it is closed, like a run the browser has gone away from."""

    def __init__(self) -> None:
        self.closed = threading.Event()

    def stream(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> Generator[AgentEvent | Finished]:
        try:
            while True:
                yield STARTED
                time.sleep(0.02)
        except GeneratorExit:
            self.closed.set()
            raise


@contextmanager
def real_server(pipeline: Any) -> Iterator[str]:
    """A real uvicorn on a free port: TestClient can't go away in the middle of a response."""
    config = uvicorn.Config(signed_in(create_app(pipeline=pipeline)), port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run)
    thread.start()
    while not server.started:
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_a_client_going_away_closes_the_run_at_once() -> None:
    # Regression: the run waited for Python's garbage collector (165 s in a real test), holding its
    # conversation's lock, with no audit line until then.
    pipeline = EndlessPipeline()

    with real_server(pipeline) as url, httpx.Client() as client:
        with client.stream("POST", f"{url}/ask/stream", json={"question": "q"}) as response:
            first = next(line for line in response.iter_lines() if line.startswith("data: "))
            assert '"step"' in first

        assert pipeline.closed.wait(5)


def test_the_docs_describe_every_event() -> None:
    spec = create_app(pipeline=FakeStreamingPipeline()).openapi()

    assert (
        "text/event-stream" in spec["paths"]["/ask/stream"]["post"]["responses"]["200"]["content"]
    )
    assert {
        "StepEvent",
        "SchemaEvent",
        "RewriteEvent",
        "ExamplesEvent",
        "SqlEvent",
        "ValidatedEvent",
        "AttemptFailedEvent",
        "CheckedEvent",
        "FinalEvent",
        "ErrorEvent",
    } <= set(spec["components"]["schemas"])


# --- The real pipeline, end to end --------------------------------------------------------------


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(graph_module, "execute_query", stub)
    monkeypatch.setattr(pipeline_module, "introspect_schema", lambda conn: SCHEMA)
    return stub


def test_a_repaired_run_end_to_end_says_what_happened_and_nothing_private(
    executor: StubExecutor,
) -> None:
    executor.replies.append(
        QueryExecutionError("The database could not run the query.", detail=PRIVATE_DETAIL)
    )
    pipeline, llm = pipeline_with(TOP, IN_1997, "Alfreds Futterkiste leads with 12345.67.")

    response = post(pipeline)

    events = events_of(response)
    reply = events[-1]["reply"]
    assert [a["kind"] for a in reply["attempts"]] == ["db_error"]
    assert set(reply["attempts"][0]) == {"sql", "kind", "message"}
    assert reply["sql_attempts"] == 2
    assert reply["schema_selection"]["tables"] == ["customers", "orders"]
    assert reply["duration_ms"] >= 0
    # The rows belong to `final` alone; the LLM's hint and its prompts belong nowhere.
    before_final = json.dumps(events[:-1])
    assert "Alfreds Futterkiste" not in before_final
    assert PRIVATE_DETAIL not in response.text
    assert describe_schema(SCHEMA) not in response.text
    assert not any(call.prompt in response.text for call in llm.calls)
