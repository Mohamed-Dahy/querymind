"""Row-level security on the real database (5.3): Postgres's own policies, end to end.

Done when: the same question returns different rows for different users. Here on Northwind
(`data/seed_northwind.py` puts the policies on orders and order_details): an Eastern regional
manager, a Western one and the workspace's admin ask the same thing through the API and get
417, 139 and 830 orders. Also: the policies fail closed, the executor's scope can't bleed into the
next query, and the scopes store.
"""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.explorer import create_explorer
from querymind.api.main import create_app
from querymind.auth.tokens import AuthUser, Role
from querymind.config import Settings
from querymind.db.connection import connect_app, connect_readonly
from querymind.db.executor import execute_query
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.row_scope import RowScope
from querymind.safety.sql_validator import validate_sql
from querymind.store.conversations import Owner, create_store
from querymind.store.row_scope import RowScopeStore
from tests.auth_helpers import signed_in, user_for

pytestmark = pytest.mark.integration

ORDERS = "SELECT count(*) FROM orders"
REVENUE = "SELECT round(sum(unit_price * quantity * (1 - discount))::numeric, 2) FROM order_details"


def run_as_analyst(sql_text: str, value: str | None) -> Any:
    """`sql_text` as the read-only role, with no startup option: only the policies decide."""
    settings = Settings()
    with psycopg.connect(settings.readonly_conninfo()) as conn:
        if value is not None:
            conn.execute("SELECT set_config('querymind.scope', %s, false)", (value,))
        row = conn.execute(sql_text).fetchone()  # fixed test SQL, no user input
        return row[0] if row else None


@pytest.mark.parametrize(
    ("value", "orders"),
    [
        (None, 0),  # never set: fail closed
        ("", 0),
        ("Nowhere", 0),
        ("eastern", 0),  # tags are exact
        ("Eastern", 417),
        ("Western", 139),
        ("Eastern,Western", 556),
        ("*", 830),
    ],
)
def test_the_policies_let_through_only_the_scopes_orders(value: str | None, orders: int) -> None:
    assert run_as_analyst(ORDERS, value) == orders


def test_order_lines_follow_their_order_so_totals_differ_per_region() -> None:
    eastern, western, every = (run_as_analyst(REVENUE, v) for v in ("Eastern", "Western", "*"))

    assert eastern == Decimal("660328.49") and western < eastern < every
    assert run_as_analyst(REVENUE, None) is None  # no line at all when the scope isn't set


def test_a_query_scope_lasts_only_for_its_query(settings: Settings) -> None:
    query = validate_sql(ORDERS, allowed_tables={"orders"}, max_rows=10)
    timeout = settings.query_timeout_seconds
    with connect_readonly(settings) as conn:
        western = execute_query(conn, query, timeout_seconds=timeout, row_scope=("Western",))
        every = execute_query(conn, query, timeout_seconds=timeout)
        after = conn.execute("SELECT current_setting('querymind.scope')").fetchone()

    assert (western.rows, every.rows) == ([[139]], [[830]])
    assert after == ("*",)  # back to the session's default, not Western


# --- The store and the done-when -----------------------------------------------------------------

AddMember = Callable[[Role, RowScope], AuthUser]


@pytest.fixture
def add_member(settings: Settings, owner: Owner) -> Iterator[AddMember]:
    """Another member of the owner's workspace (until 5.12, members are added by hand)."""
    added: list[UUID] = []

    def add(role: Role, scope: RowScope) -> AuthUser:
        user_id = uuid4()
        with connect_app(settings) as conn:
            conn.execute(
                "INSERT INTO querymind_app.users (id, email, password_hash) VALUES (%s, %s, 'x')",
                (user_id, f"member-{user_id}@example.com"),
            )
            conn.execute(
                "INSERT INTO querymind_app.memberships (workspace_id, user_id, role, row_scope)"
                " VALUES (%s, %s, %s, %s)",
                (owner.workspace_id, user_id, role, None if scope is None else list(scope)),
            )
        added.append(user_id)
        return AuthUser(
            user_id=user_id, workspace_id=owner.workspace_id, role=role, email="m@example.com"
        )

    yield add
    with connect_app(settings) as conn:
        conn.execute("DELETE FROM querymind_app.users WHERE id = ANY(%s)", (added,))


def test_the_store_reads_and_sets_a_members_scope(
    settings: Settings, owner: Owner, add_member: AddMember
) -> None:
    store = RowScopeStore(settings)
    viewer = add_member("viewer", None)

    assert store.scope(owner.workspace_id, viewer.user_id, "viewer") is None  # every row
    assert store.set(owner.workspace_id, viewer.user_id, ("Eastern", "Western"))
    assert store.scope(owner.workspace_id, viewer.user_id, "viewer") == ("Eastern", "Western")
    assert store.member(owner.workspace_id, viewer.user_id) == ("viewer", ("Eastern", "Western"))
    assert store.scope(owner.workspace_id, uuid4(), "viewer") == ()  # not a member: no row
    assert not store.set(owner.workspace_id, uuid4(), None)


def test_an_empty_scope_is_refused_by_the_database_too(
    settings: Settings, owner: Owner, add_member: AddMember
) -> None:
    viewer = add_member("viewer", None)
    with connect_app(settings) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "UPDATE querymind_app.memberships SET row_scope = '{}' WHERE user_id = %s",
            (viewer.user_id,),
        )


def client_for(settings: Settings, user: AuthUser, sql_text: str) -> TestClient:
    draft = SqlDraft(sql=sql_text, explanation="Counts.", tables_used=["orders"])
    own = settings.model_copy(update={"schema_retrieval": "off"})
    app = create_app(
        pipeline=Pipeline(llm=FakeLLMProvider([draft, "The count."]), settings=own),
        explorer=create_explorer(own),
        store=create_store(own),
        row_scopes=RowScopeStore(own),
    )
    return TestClient(signed_in(app, user), raise_server_exceptions=False)


def ask_and_rerun(api: TestClient) -> tuple[Any, Any]:
    with api:
        answer = api.post("/ask", json={"question": "How many orders do we have?"}).json()
        conversation = answer["conversation_id"]
        rerun = api.post(f"/conversations/{conversation}/turns/1/rerun").json()
    return answer["rows"], rerun["rows"]


def test_done_when_the_same_question_returns_different_rows_for_different_users(
    settings: Settings, owner: Owner, add_member: AddMember
) -> None:
    eastern = add_member("viewer", ("Eastern",))
    western = add_member("analyst", ("Western",))
    admin = user_for(owner, "admin")

    results = {
        name: ask_and_rerun(client_for(settings, user, ORDERS))
        for name, user in (("eastern", eastern), ("western", western), ("admin", admin))
    }

    assert results == {
        "eastern": ([[417]], [[417]]),  # the answer and its re-run
        "western": ([[139]], [[139]]),
        "admin": ([[830]], [[830]]),
    }


def test_a_scope_change_applies_to_the_next_question(
    settings: Settings, owner: Owner, add_member: AddMember
) -> None:
    viewer = add_member("viewer", ("Eastern",))
    first, _ = ask_and_rerun(client_for(settings, viewer, REVENUE))
    RowScopeStore(settings).set(owner.workspace_id, viewer.user_id, ("Western",))
    second, _ = ask_and_rerun(client_for(settings, viewer, REVENUE))

    assert first != second  # read fresh: not from the login token
