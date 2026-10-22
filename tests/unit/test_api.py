"""The HTTP API with a fake pipeline: routes, response shape, and clean errors. No DB, no LLM."""

import logging

import pytest
from fastapi.testclient import TestClient

from querymind import __version__
from querymind.agent.schemas import (
    AgentReply,
    AnswerSummary,
    AskResult,
    Clarification,
    ClarificationRequest,
    Refusal,
    SanityWarning,
)
from querymind.api.main import create_app
from querymind.api.schemas import AskResponse
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.db.executor import QueryResult
from querymind.llm import LLMOutputError, LLMProviderError
from querymind.logging_setup import JsonFormatter
from querymind.safety.errors import UnsafeSqlError

RESULT = AskResult(
    question="How many customers are there?",
    sql="SELECT count(*) AS customer_count FROM customers LIMIT 1001",
    explanation="Counts the customers.",
    result=QueryResult(
        columns=["customer_count"], rows=[[91]], row_count=1, truncated=False, duration_ms=3
    ),
    answer=AnswerSummary(text="There are 91 customers.", from_llm=True),
)


class FakePipeline:
    """Returns `reply` (RESULT by default), or raises `error`. Records the questions asked."""

    def __init__(self, error: Exception | None = None, reply: AgentReply = RESULT) -> None:
        self.error = error
        self.reply = reply
        self.questions: list[str] = []

    def ask(self, question: str) -> AgentReply:
        self.questions.append(question)
        if self.error is not None:
            raise self.error
        return self.reply


def client_for(pipeline: FakePipeline) -> TestClient:
    # raise_server_exceptions=False: behave like a real server, which answers 500 instead of
    # crashing the test when a route raises an unexpected error.
    return TestClient(create_app(pipeline=pipeline), raise_server_exceptions=False)


def test_ask_returns_answer_sql_and_rows() -> None:
    pipeline = FakePipeline()
    with client_for(pipeline) as client:
        response = client.post("/ask", json={"question": "  How many customers are there?  "})
    assert response.status_code == 200
    assert response.json() == {
        "kind": "answer",
        "question": "How many customers are there?",
        "answer": "There are 91 customers.",
        "answer_from_llm": True,
        "sql": "SELECT count(*) AS customer_count FROM customers LIMIT 1001",
        "explanation": "Counts the customers.",
        "columns": ["customer_count"],
        "rows": [[91]],
        "row_count": 1,
        "truncated": False,
        "warnings": [],
    }
    assert pipeline.questions == ["How many customers are there?"]  # whitespace stripped


def test_warnings_are_sent_without_the_llm_hint() -> None:
    warning = SanityWarning(
        code="duplicate_rows",
        message="Some rows appear more than once.",
        detail="2 of the 4 rows are exact duplicates... use DISTINCT.",
    )
    response = AskResponse.from_result(RESULT.model_copy(update={"warnings": (warning,)}))
    assert [w.model_dump() for w in response.warnings] == [
        {"code": "duplicate_rows", "message": "Some rows appear more than once."}
    ]


def test_health() -> None:
    with client_for(FakePipeline()) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"question": ""}, "question: String should have at least 1 character"),
        ({"question": "   "}, "question: String should have at least 1 character"),
        ({"question": "x" * 1001}, "question: String should have at most 1000 characters"),
        ({}, "question: Field required"),
        ({"question": 42}, "question: Input should be a valid string"),
    ],
)
def test_invalid_requests_get_a_clean_422(body: dict[str, object], message: str) -> None:
    pipeline = FakePipeline()
    with client_for(pipeline) as client:
        response = client.post("/ask", json=body)
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_request", "message": message}
    assert pipeline.questions == []  # never reached the pipeline


@pytest.mark.parametrize(
    ("error", "status", "code", "message"),
    [
        (UnsafeSqlError("Only SELECT queries are allowed."), 422, "unsafe_sql",
         "Only SELECT queries are allowed."),
        (QueryTimeoutError("The query took longer than 10 s and was stopped."), 504,
         "query_timeout", "The query took longer than 10 s and was stopped."),
        (QueryExecutionError("The database could not run the query: x", detail="x"), 502,
         "query_failed", "The database could not run the query: x"),
        (DatabaseUnavailableError("Could not connect to the database. Please try again later."),
         503, "db_unavailable", "Could not connect to the database. Please try again later."),
        (LLMProviderError("groq request failed: rate limited"), 502, "llm_unavailable",
         "The AI provider did not respond (network, rate limit or outage)."),
        (LLMOutputError("groq output did not match the schema"), 502, "llm_bad_output",
         "The AI model declined or answered in the wrong format. Try rewording it."),
    ],
)  # fmt: skip
def test_known_errors_map_to_clean_responses(
    error: Exception, status: int, code: str, message: str
) -> None:
    with client_for(FakePipeline(error)) as client:
        response = client.post("/ask", json={"question": "anything"})
    assert response.status_code == status
    assert response.json() == {"error": code, "message": message}


def test_llm_vendor_details_stay_in_the_server_log(caplog: pytest.LogCaptureFixture) -> None:
    raw = "groq request failed: Error code: 400 - {'error': {'code': 'json_validate_failed'}}"
    with caplog.at_level(logging.WARNING), client_for(FakePipeline(LLMOutputError(raw))) as client:
        response = client.post("/ask", json={"question": "anything"})
    assert response.json()["error"] == "llm_bad_output"
    assert "json_validate_failed" not in response.text
    assert any(getattr(r, "detail", None) == raw for r in caplog.records)  # kept for debugging


def test_unexpected_errors_never_leak_details(caplog: pytest.LogCaptureFixture) -> None:
    secret = "password=hunter2 at /srv/app/db.py line 42"
    with caplog.at_level(logging.ERROR), client_for(FakePipeline(KeyError(secret))) as client:
        response = client.post("/ask", json={"question": "anything"})
    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_error",
        "message": "Something went wrong. Please try again.",
    }
    assert "hunter2" not in response.text
    assert "unhandled error" in caplog.text  # ...but the server log has it, for debugging


def test_openapi_docs_describe_the_endpoints() -> None:
    with client_for(FakePipeline()) as client:
        spec = client.get("/openapi.json").json()
        docs_page = client.get("/docs")
    assert docs_page.status_code == 200
    assert set(spec["paths"]) == {"/ask", "/health"}
    assert set(spec["paths"]["/ask"]["post"]["responses"]) >= {"200", "422", "502", "504"}


def test_json_log_lines_include_extra_fields() -> None:
    record = logging.makeLogRecord(
        {"name": "querymind.audit", "levelname": "INFO", "msg": "ask", "outcome": "ok"}
    )
    line = JsonFormatter().format(record)
    assert '"message": "ask"' in line
    assert '"outcome": "ok"' in line
    assert '"logger": "querymind.audit"' in line


def test_a_clarifying_question_is_a_200_with_its_options() -> None:
    clarification = Clarification(
        question="Best by what?", options=["by total revenue", "by number of orders"]
    )
    reply = ClarificationRequest(question="Best customers?", clarification=clarification)
    with client_for(FakePipeline(reply=reply)) as client:
        response = client.post("/ask", json={"question": "Best customers?"})
    assert response.status_code == 200  # not an error: the agent is asking back
    assert response.json() == {
        "kind": "clarification",
        "question": "Best customers?",
        "clarifying_question": "Best by what?",
        "options": ["by total revenue", "by number of orders"],
    }


def test_a_refusal_is_a_200_with_its_message() -> None:
    reply = Refusal(question="Delete all orders", message="I can only read data.")
    with client_for(FakePipeline(reply=reply)) as client:
        response = client.post("/ask", json={"question": "Delete all orders"})
    assert response.status_code == 200
    assert response.json() == {
        "kind": "refusal",
        "question": "Delete all orders",
        "message": "I can only read data.",
    }
