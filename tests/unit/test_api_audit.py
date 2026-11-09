"""The audit log from the routes (5.5): one line per question, re-run, sign-up, login and logout,
however it ended, and the admin-only page that reads it."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from querymind.agent.schemas import Clarification, ClarificationRequest
from querymind.api.main import create_app
from querymind.api.routes import ask_stream
from querymind.api.schemas import AskRequest
from querymind.llm import LLMProviderError
from querymind.store.audit import AuditEntry, AuditRecord
from querymind.store.errors import StoreUnavailableError
from tests.auth_helpers import OTHER_USER, TEST_USER, make_tokens, signed_in
from tests.unit.test_api import RESULT, FakePipeline
from tests.unit.test_api_auth import COOKIES, PASSWORD, FakeAccounts, signed_up
from tests.unit.test_api_conversations import ID
from tests.unit.test_api_conversations import FakePipeline as FakeRerunPipeline
from tests.unit.test_api_conversations import FakeStore as FakeConversationStore
from tests.unit.test_api_saving import FakePipeline as FakeStreamingPipeline
from tests.unit.test_api_saving import FakeStore as FakeSavingStore

RECORD = AuditRecord(
    id=7,
    at=datetime(2026, 11, 9, 10, 0, tzinfo=UTC),
    action="ask",
    outcome="unsafe_sql",
    user_id=TEST_USER.user_id,
    workspace_id=TEST_USER.workspace_id,
    email=TEST_USER.email,
    question="Delete everything",
    sql="DELETE FROM orders",
    detail="Only SELECT queries are allowed.",
)


class FakeAudit:
    """Keeps the lines it is given; `list` answers one page. Or fails."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.lines: list[AuditEntry] = []
        self.asked: list[tuple[UUID, dict[str, Any]]] = []

    def record(self, entry: AuditEntry) -> None:
        if self.error is not None:
            raise self.error
        self.lines.append(entry)

    def list(self, workspace_id: UUID, **filters: Any) -> tuple[list[AuditRecord], bool]:
        self.asked.append((workspace_id, filters))
        return [RECORD], True


def client(audit: FakeAudit, **parts: Any) -> TestClient:
    app = signed_in(create_app(audit=audit, **parts))  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


# --- Questions --------------------------------------------------------------------------------


def test_an_answer_is_one_line_with_who_what_and_the_sql() -> None:
    audit = FakeAudit()
    with client(audit, pipeline=FakePipeline()) as api:
        body = api.post("/ask", json={"question": "How many customers are there?"}).json()

    [line] = audit.lines
    assert line.action == "ask" and line.outcome == "answer"
    assert (line.user_id, line.workspace_id, line.email) == (
        TEST_USER.user_id,
        TEST_USER.workspace_id,
        TEST_USER.email,
    )
    assert str(line.conversation_id) == body["conversation_id"]
    assert line.question == "How many customers are there?"
    assert line.sql == RESULT.sql and line.row_count == 1 and line.sql_attempts == 1
    assert line.duration_ms is not None and line.duration_ms >= 0


def test_a_question_back_is_its_own_outcome() -> None:
    asks_back = ClarificationRequest(
        question="Best customers?",
        clarification=Clarification(question="By what?", options=["Revenue", "Orders"]),
    )
    audit = FakeAudit()
    with client(audit, pipeline=FakePipeline(reply=asks_back)) as api:
        api.post("/ask", json={"question": "Best customers?"})

    assert [(line.outcome, line.sql) for line in audit.lines] == [("clarification", None)]


def test_a_failed_question_is_written_with_its_error_code() -> None:
    audit = FakeAudit()
    with client(audit, pipeline=FakePipeline(error=LLMProviderError("raw vendor text"))) as api:
        response = api.post("/ask", json={"question": "q"})

    assert response.status_code == 502
    [line] = audit.lines
    assert line.outcome == "llm_unavailable"
    assert line.detail == "The AI provider did not respond (network or outage)."
    assert "raw vendor text" not in str(line)  # the vendor's reply stays in the server log


def test_trying_someone_elses_conversation_is_written_too() -> None:
    store = FakeSavingStore()
    store.foreign.add(ID)
    audit = FakeAudit()
    with client(audit, pipeline=FakePipeline(), store=store) as api:
        api.post("/ask", json={"question": "q", "conversation_id": str(ID)})

    assert [line.outcome for line in audit.lines] == ["conversation_not_found"]


def test_an_audit_failure_never_costs_the_answer() -> None:
    audit = FakeAudit(error=StoreUnavailableError("down"))
    with client(audit, pipeline=FakePipeline()) as api:
        response = api.post("/ask", json={"question": "q"})

    assert response.status_code == 200
    assert response.json()["answer"] == "There are 91 customers."


@pytest.mark.parametrize(
    ("pipeline", "outcome"),
    [
        (FakeStreamingPipeline(), "answer"),
        (FakeStreamingPipeline(error=LLMProviderError("x")), "llm_unavailable"),
    ],
)
def test_the_stream_writes_one_line_at_the_end(pipeline: Any, outcome: str) -> None:
    audit = FakeAudit()
    with client(audit, pipeline=pipeline) as api:
        api.post("/ask/stream", json={"question": "q"})

    assert [line.outcome for line in audit.lines] == [outcome]


def test_a_stopped_stream_is_written_as_cancelled() -> None:
    audit = FakeAudit()
    events = ask_stream(
        AskRequest(question="q"),
        TEST_USER,
        FakeStreamingPipeline(),
        [],
        None,
        audit,  # type: ignore[arg-type]
        None,
    )

    next(events)  # the first step arrived, then the client went away
    events.close()

    assert [(line.outcome, line.question) for line in audit.lines] == [("cancelled", "q")]


# --- Re-runs ----------------------------------------------------------------------------------


def test_a_rerun_is_written_with_the_sql_it_ran() -> None:
    audit = FakeAudit()
    with client(audit, pipeline=FakeRerunPipeline(), store=FakeConversationStore()) as api:
        api.post(f"/conversations/{ID}/turns/1/rerun")
        api.post(f"/conversations/{ID}/turns/9/rerun")  # no such turn

    ok, missing = audit.lines
    assert (ok.action, ok.outcome, ok.question) == ("rerun", "answer", "Top customers?")
    assert ok.sql is not None and ok.row_count is not None
    assert (missing.action, missing.outcome) == ("rerun", "turn_not_found")


# --- Logins -----------------------------------------------------------------------------------


def auth_client(accounts: FakeAccounts, audit: FakeAudit) -> TestClient:
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        tokens=make_tokens(),
        auth_cookie=COOKIES,
        audit=audit,  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


def test_sign_up_login_and_logout_are_written() -> None:
    audit = FakeAudit()

    class EndingAccounts(FakeAccounts):
        def end_session(self, token_hash: str) -> tuple[UUID, UUID, str] | None:  # type: ignore[override]
            super().end_session(token_hash)
            return TEST_USER.user_id, TEST_USER.workspace_id, "new@example.com"

    accounts = EndingAccounts()
    with auth_client(accounts, audit) as api:
        api.post("/auth/signup", json={"email": "new@example.com", "password": PASSWORD})
        api.post("/auth/login", json={"email": "new@example.com", "password": PASSWORD})
        api.post("/auth/logout")

    assert [(line.action, line.outcome, line.email) for line in audit.lines] == [
        ("signup", "ok", "new@example.com"),
        ("login", "ok", "new@example.com"),
        ("logout", "ok", "new@example.com"),
    ]
    assert all(line.user_id is not None for line in audit.lines)


def test_failed_logins_keep_the_email_typed_and_the_account_if_there_is_one() -> None:
    accounts, audit = FakeAccounts(), FakeAudit()
    signed_up(accounts)
    with auth_client(accounts, audit) as api:
        api.post("/auth/login", json={"email": "ANA@example.com", "password": "wrong password!"})
        api.post("/auth/login", json={"email": "nobody@example.com", "password": "guess guess"})
        api.post("/auth/signup", json={"email": "ana@example.com", "password": PASSWORD})

    wrong, unknown, taken = audit.lines
    assert (wrong.action, wrong.outcome, wrong.email) == (
        "login_failed",
        "invalid_login",
        "ana@example.com",
    )
    assert wrong.user_id is not None  # a real account under attack: shown to its admin
    assert (unknown.email, unknown.user_id, unknown.workspace_id) == (
        "nobody@example.com",
        None,
        None,
    )
    assert (taken.action, taken.outcome) == ("signup", "email_taken")


# --- GET /admin/audit -------------------------------------------------------------------------


def admin_client(audit: FakeAudit | None, user: Any = TEST_USER) -> TestClient:
    app = signed_in(create_app(pipeline=object(), audit=audit), user)  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def test_an_admin_reads_their_own_workspace_with_filters() -> None:
    audit = FakeAudit()
    with admin_client(audit) as api:
        response = api.get(
            "/admin/audit",
            params={
                "outcome": "unsafe_sql",
                "email": "Ana@Example.com",
                "q": "DELETE",
                "before": 50,
                "limit": 20,
            },
        )

    assert response.status_code == 200
    [(workspace, filters)] = audit.asked
    assert workspace == TEST_USER.workspace_id  # from the token, never from the request
    assert filters == {
        "action": None,
        "outcome": "unsafe_sql",
        "email": "ana@example.com",
        "search": "DELETE",
        "before": 50,
        "limit": 20,
    }
    body = response.json()
    assert body["has_more"] is True
    [line] = body["lines"]
    assert (
        line["sql"] == "DELETE FROM orders" and line["detail"] == "Only SELECT queries are allowed."
    )
    assert "user_id" not in line and "workspace_id" not in line


def test_only_admins_may_read_it() -> None:
    audit = FakeAudit()
    with admin_client(audit, user=OTHER_USER) as api:  # an analyst
        response = api.get("/admin/audit")

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert audit.asked == []


def test_an_unknown_action_is_a_clean_422() -> None:
    with admin_client(FakeAudit()) as api:
        response = api.get("/admin/audit", params={"action": "hack"})

    assert response.status_code == 422


def test_without_an_audit_store_it_says_so() -> None:
    with admin_client(None) as api:
        response = api.get("/admin/audit")

    assert response.status_code == 503
