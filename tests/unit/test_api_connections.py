"""The /connections routes (5.9): nothing is saved before the checks pass, a refusal says why,
only admins change anything, checks are rate-limited, and no reply ever holds the password."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.api import connections as connection_routes
from querymind.api.main import create_app
from querymind.api.ratelimit import RateLimits
from querymind.auth.tokens import AuthUser
from querymind.db.errors import RemoteUnreachableError, WritableLoginError
from querymind.db.remote import ConnectionSpec
from querymind.store.audit import AuditEntry
from querymind.store.connections import ConnectionRecord
from querymind.store.errors import ConnectionNameTakenError, ConnectionNotFoundError
from tests.auth_helpers import OTHER_USER, TEST_USER, signed_in
from tests.unit.test_api import FakePipeline

PASSWORD = "correct-horse-battery-staple"
BODY = {
    "name": "Sales",
    "host": "db.example.com",
    "port": 5432,
    "dbname": "sales",
    "user": "reader",
    "password": PASSWORD,
}


class MemoryConnections:
    """Stands in for ConnectionStore."""

    def __init__(self) -> None:
        self.saved: dict[UUID, tuple[UUID, ConnectionRecord, str]] = {}

    def save(
        self, workspace_id: UUID, name: str, spec: ConnectionSpec, password: SecretStr
    ) -> ConnectionRecord:
        if any(w == workspace_id and r.name == name for w, r, _ in self.saved.values()):
            raise ConnectionNameTakenError(
                f"This workspace already has a database called {name!r}."
            )
        now = datetime.now(UTC)
        record = ConnectionRecord(
            id=uuid4(),
            name=name,
            host=spec.host,
            port=spec.port,
            dbname=spec.dbname,
            username=spec.user,
            sslmode=spec.sslmode,
            secret_id=uuid4(),
            checked_at=now,
            created_at=now,
        )
        self.saved[record.id] = (workspace_id, record, password.get_secret_value())
        return record

    def list(self, workspace_id: UUID) -> list[ConnectionRecord]:
        return [r for w, r, _ in self.saved.values() if w == workspace_id]

    def get(self, workspace_id: UUID, connection_id: UUID) -> ConnectionRecord:
        found = self.saved.get(connection_id)
        if found is None or found[0] != workspace_id:
            raise ConnectionNotFoundError("No database connection with this id.")
        return found[1]

    def password(self, workspace_id: UUID, record: ConnectionRecord) -> SecretStr:
        return SecretStr(self.saved[record.id][2])

    def mark_checked(self, workspace_id: UUID, connection_id: UUID) -> ConnectionRecord:
        return self.get(workspace_id, connection_id)

    def delete(self, workspace_id: UUID, connection_id: UUID) -> None:
        self.get(workspace_id, connection_id)
        del self.saved[connection_id]


class MemoryAudit:
    def __init__(self) -> None:
        self.lines: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.lines.append(entry)


def app_with(
    store: MemoryConnections, audit: MemoryAudit | None = None, limits: RateLimits | None = None
) -> FastAPI:
    return create_app(
        pipeline=FakePipeline(),
        connections=store,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        limits=limits,
    )


@pytest.fixture
def checks(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """The connect-and-check step: queue an error to raise, or nothing for "it passed"."""
    queued: list[object] = []

    def fake_check(spec: ConnectionSpec, password: SecretStr, *, allow_private: bool) -> None:
        assert allow_private is False  # never on unless the server's settings say so
        if queued and isinstance(queued[0], Exception):
            raise queued.pop(0)  # type: ignore[misc]

    monkeypatch.setattr(connection_routes, "_check", fake_check)
    return queued


def test_a_checked_database_is_saved_and_shown_without_its_password(
    checks: list[object],
) -> None:
    store, audit = MemoryConnections(), MemoryAudit()
    with TestClient(signed_in(app_with(store, audit))) as client:
        saved = client.post("/connections", json=BODY)
        listed = client.get("/connections")

    assert saved.status_code == 201
    assert saved.json()["sslmode"] == "verify-full"  # the default: certificate checked
    assert [c["name"] for c in listed.json()] == ["Sales"]
    for response in (saved, listed):
        assert PASSWORD not in response.text
        assert "password" not in response.text
    [line] = audit.lines
    assert (line.action, line.outcome, line.detail) == (
        "connection_saved",
        "ok",
        "reader@db.example.com:5432/sales",
    )


def test_a_writable_login_is_refused_with_its_fix_and_not_saved(checks: list[object]) -> None:
    problem = 'The login can create tables in schema public. Run: REVOKE CREATE ON SCHEMA "public"'
    checks.append(
        WritableLoginError(f"This login can change your database. {problem}", problems=[problem])
    )
    store, audit = MemoryConnections(), MemoryAudit()
    with TestClient(signed_in(app_with(store, audit))) as client:
        refused = client.post("/connections", json=BODY)

    assert refused.status_code == 422
    assert refused.json()["error"] == "connection_writable"
    assert "REVOKE CREATE ON SCHEMA" in refused.json()["message"]
    assert store.saved == {}
    [line] = audit.lines
    assert (line.action, line.outcome) == ("connection_refused", "connection_writable")


def test_a_private_address_is_refused_before_any_connection() -> None:
    """The real check, no fake: 127.0.0.1 never gets a connection attempt."""
    store = MemoryConnections()
    with TestClient(signed_in(app_with(store))) as client:
        refused = client.post("/connections", json=BODY | {"host": "127.0.0.1"})

    assert refused.status_code == 422
    assert refused.json()["error"] == "connection_unsafe"
    assert store.saved == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"dbname": "host=10.0.0.5"},
        {"host": "db.example.com/x"},
        {"sslmode": "allow"},  # not offered
        {"options": "-c default_transaction_read_only=off"},  # no such field
        {"password": "x" * 2000},
    ],
)
def test_a_bad_request_is_refused_without_echoing_the_password(
    checks: list[object], fields: dict[str, object]
) -> None:
    with TestClient(signed_in(app_with(MemoryConnections()))) as client:
        refused = client.post("/connections", json=BODY | fields)

    assert refused.status_code == 422
    assert refused.json()["error"] == "invalid_request"
    assert PASSWORD not in refused.text


def test_only_an_admin_can_add_test_or_delete(checks: list[object]) -> None:
    store = MemoryConnections()
    saved = store.save(
        TEST_USER.workspace_id,
        "Sales",
        ConnectionSpec(host="db.example.com", dbname="sales", user="reader"),
        SecretStr(PASSWORD),
    )
    analyst = AuthUser(
        user_id=uuid4(), workspace_id=TEST_USER.workspace_id, role="analyst", email="a@x.com"
    )
    with TestClient(signed_in(app_with(store), analyst)) as client:
        assert client.post("/connections", json=BODY | {"name": "Other"}).status_code == 403
        assert client.post(f"/connections/{saved.id}/test").status_code == 403
        assert client.delete(f"/connections/{saved.id}").status_code == 403
        assert client.get("/connections").status_code == 200  # but may see them


def test_the_same_name_twice_is_a_409(checks: list[object]) -> None:
    with TestClient(signed_in(app_with(MemoryConnections()))) as client:
        assert client.post("/connections", json=BODY).status_code == 201
        taken = client.post("/connections", json=BODY)

    assert taken.status_code == 409
    assert taken.json()["error"] == "connection_name_taken"


def test_retesting_finds_a_database_that_became_unreachable(checks: list[object]) -> None:
    store, audit = MemoryConnections(), MemoryAudit()
    with TestClient(signed_in(app_with(store, audit))) as client:
        saved = client.post("/connections", json=BODY).json()
        checks.append(RemoteUnreachableError("Couldn't reach the database at this host and port."))
        failed = client.post(f"/connections/{saved['id']}/test")
        passed = client.post(f"/connections/{saved['id']}/test")

    assert failed.status_code == 422 and failed.json()["error"] == "connection_unreachable"
    assert passed.status_code == 200
    assert [line.action for line in audit.lines] == [
        "connection_saved",
        "connection_refused",
        "connection_tested",
    ]


def test_another_workspaces_database_is_not_found(checks: list[object]) -> None:
    store = MemoryConnections()
    app = app_with(store)
    with TestClient(app) as client:
        signed_in(app, TEST_USER)
        saved = client.post("/connections", json=BODY).json()
        signed_in(app, OTHER_USER.model_copy(update={"role": "admin"}))
        assert client.get("/connections").json() == []
        assert client.post(f"/connections/{saved['id']}/test").status_code == 404
        assert client.delete(f"/connections/{saved['id']}").status_code == 404
        signed_in(app, TEST_USER)
        assert client.delete(f"/connections/{saved['id']}").status_code == 204
        assert client.get("/connections").json() == []


def test_checks_are_rate_limited(checks: list[object]) -> None:
    """Each check makes our server connect to a typed address: it can't become a port scanner."""
    limits = RateLimits(
        questions_per_minute=10,
        questions_per_day=10,
        questions_total_per_day=10,
        reruns_per_minute=10,
        logins_per_ip_per_minute=10,
        logins_per_email_per_minute=10,
        signups_per_ip_per_hour=10,
        connection_checks_per_hour=2,
    )
    with TestClient(signed_in(app_with(MemoryConnections(), limits=limits))) as client:
        statuses = [
            client.post("/connections", json=BODY | {"name": f"Db {n}"}).status_code
            for n in range(3)
        ]

    assert statuses == [201, 201, 429]
