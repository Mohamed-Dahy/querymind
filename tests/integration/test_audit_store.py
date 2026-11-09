"""The audit log on the real database (5.5): written, read per workspace, and never rewritten."""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors
from setup_app_db import SCHEMA

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.store.audit import AuditEntry, AuditStore
from querymind.store.conversations import Owner

pytestmark = pytest.mark.integration


@pytest.fixture
def audit(settings: Settings, owner: Owner, other_owner: Owner) -> Iterator[AuditStore]:
    """The store; afterwards the admin removes this test's lines (the app's role can't)."""
    yield AuditStore(settings)
    with psycopg.connect(settings.admin_conninfo()) as admin:
        admin.execute(
            f"DELETE FROM {SCHEMA}.audit_log WHERE workspace_id IN (%s, %s) OR email LIKE %s",
            (owner.workspace_id, other_owner.workspace_id, "audit-test-%@example.com"),
        )


def line(owner: Owner, **fields: object) -> AuditEntry:
    base: dict[str, object] = {
        "action": "ask",
        "outcome": "answer",
        "user_id": owner.user_id,
        "workspace_id": owner.workspace_id,
        "email": "ana@example.com",
        "question": "Top customers?",
        "sql": "SELECT company_name FROM customers LIMIT 1001",
        "tables": ("customers",),
        "row_count": 91,
        "duration_ms": 1200,
        "sql_attempts": 1,
    }
    return AuditEntry.model_validate(base | fields)


def test_a_line_is_written_and_read_back_whole(audit: AuditStore, owner: Owner) -> None:
    audit.record(line(owner))

    [found], has_more = audit.list(owner.workspace_id)

    assert not has_more
    assert found.model_dump(exclude={"id", "at"}) == line(owner).model_dump()
    assert found.at is not None and found.id > 0


def test_newest_first_with_pages_that_dont_shift(audit: AuditStore, owner: Owner) -> None:
    for number in range(5):
        audit.record(line(owner, question=f"Question {number}?"))

    first, more = audit.list(owner.workspace_id, limit=2)
    second, _ = audit.list(owner.workspace_id, limit=2, before=first[-1].id)
    audit.record(line(owner, question="Newer, while paging"))
    third, last_more = audit.list(owner.workspace_id, limit=2, before=second[-1].id)

    assert [x.question for x in first + second + third] == [
        f"Question {n}?" for n in (4, 3, 2, 1, 0)
    ]
    assert more and not last_more


def test_filters(audit: AuditStore, owner: Owner) -> None:
    audit.record(line(owner))
    audit.record(line(owner, outcome="unsafe_sql", sql="DELETE FROM orders", question="Wipe it"))
    audit.record(
        line(owner, action="login_failed", outcome="invalid_login", email="eve@example.com")
    )

    def outcomes(**filters: object) -> list[str]:
        return [x.outcome for x in audit.list(owner.workspace_id, **filters)[0]]  # type: ignore[arg-type]

    assert outcomes(outcome="unsafe_sql") == ["unsafe_sql"]
    assert outcomes(action="login_failed") == ["invalid_login"]
    assert outcomes(email="eve@example.com") == ["invalid_login"]
    assert outcomes(search="delete from") == ["unsafe_sql"]  # in the SQL, any case
    assert outcomes(search="wipe") == ["unsafe_sql"]  # in the question
    assert outcomes(search="%") == []  # a LIKE wildcard means itself


def test_each_workspace_sees_only_its_own(
    audit: AuditStore, owner: Owner, other_owner: Owner
) -> None:
    audit.record(line(owner))
    audit.record(line(other_owner, question="Theirs"))

    assert [x.question for x in audit.list(owner.workspace_id)[0]] == ["Top customers?"]
    assert [x.question for x in audit.list(other_owner.workspace_id)[0]] == ["Theirs"]


def test_an_unknown_email_failing_to_log_in_belongs_to_no_workspace(
    settings: Settings, audit: AuditStore, owner: Owner
) -> None:
    email = f"audit-test-{uuid4()}@example.com"
    audit.record(AuditEntry(action="login_failed", outcome="invalid_login", email=email))

    assert email not in {x.email for x in audit.list(owner.workspace_id)[0]}
    with psycopg.connect(settings.admin_conninfo()) as admin:  # but it is written
        row = admin.execute(
            f"SELECT workspace_id FROM {SCHEMA}.audit_log WHERE email = %s", (email,)
        ).fetchone()
    assert row == (None,)


@pytest.mark.parametrize(
    "statement",
    [
        f"UPDATE {SCHEMA}.audit_log SET outcome = 'answer'",
        f"DELETE FROM {SCHEMA}.audit_log",
        f"TRUNCATE {SCHEMA}.audit_log",
    ],
)
def test_the_app_can_add_lines_but_never_change_them(
    settings: Settings, audit: AuditStore, owner: Owner, statement: str
) -> None:
    audit.record(line(owner, outcome="unsafe_sql"))

    with connect_app(settings) as conn, pytest.raises(errors.InsufficientPrivilege):
        conn.execute(statement)


def test_deleting_a_user_keeps_their_lines(
    settings: Settings, audit: AuditStore, owner: Owner
) -> None:
    audit.record(line(owner))
    with connect_app(settings) as conn:
        conn.execute(f"DELETE FROM {SCHEMA}.users WHERE id = %s", (owner.user_id,))

    [kept] = audit.list(owner.workspace_id)[0]
    assert kept.user_id is None and kept.email == "ana@example.com"


def test_the_agents_role_cant_read_the_audit_log(ro_conn: psycopg.Connection) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(f"SELECT * FROM {SCHEMA}.audit_log")
