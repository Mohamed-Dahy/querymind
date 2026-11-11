"""Table permissions on the real database (5.2): the store's rules, and the done-when end to end.

Done when: a viewer can't query or discover restricted tables, in answers and in the explorer.
Here: in a throwaway workspace, viewers may use every demo table but `customers`. A viewer's
question is answered from a prompt with no `customers` table, SQL on it is refused, and the
explorer doesn't list it; the workspace's admin asks the same and gets the answer.
"""

from uuid import UUID

import psycopg
import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.explorer import create_explorer
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.llm.fake import FakeLLMProvider
from querymind.store.connections import ConnectionStore
from querymind.store.conversations import Owner, create_store
from querymind.store.table_access import TableAccessStore
from tests.auth_helpers import signed_in, user_for
from tests.integration.test_onboarding_db import connections as connections  # fixture
from tests.integration.test_onboarding_db import saved_id
from tests.integration.test_remote_databases import admin as admin  # fixture

pytestmark = pytest.mark.integration


@pytest.fixture
def access(settings: Settings) -> TableAccessStore:
    return TableAccessStore(settings)


def test_a_rule_is_per_workspace_database_and_role(
    access: TableAccessStore,
    connections: ConnectionStore,
    owner: Owner,
    other_owner: Owner,
) -> None:
    mine = saved_id(connections, owner)
    access.set(owner.workspace_id, None, "viewer", ["orders", "orders", "products"])  # the demo
    access.set(owner.workspace_id, mine, "analyst", [])

    assert access.allowed(owner.workspace_id, None, "viewer") == frozenset({"orders", "products"})
    assert access.allowed(owner.workspace_id, None, "analyst") is None  # no rule: every table
    assert access.allowed(owner.workspace_id, None, "admin") is None  # never restricted
    assert access.allowed(owner.workspace_id, mine, "analyst") == frozenset()
    assert access.allowed(owner.workspace_id, mine, "viewer") is None
    assert access.allowed(other_owner.workspace_id, None, "viewer") is None  # their workspace
    assert access.rules(owner.workspace_id, None) == {
        "analyst": None,
        "viewer": ["orders", "products"],
    }


def test_setting_again_replaces_the_rule_and_null_lifts_it(
    access: TableAccessStore, owner: Owner
) -> None:
    # One demo row per role, though connection_id is NULL (UNIQUE NULLS NOT DISTINCT).
    access.set(owner.workspace_id, None, "viewer", ["orders"])
    access.set(owner.workspace_id, None, "viewer", ["products"])
    assert access.allowed(owner.workspace_id, None, "viewer") == frozenset({"products"})

    access.set(owner.workspace_id, None, "viewer", None)

    assert access.allowed(owner.workspace_id, None, "viewer") is None


def test_deleting_the_database_deletes_its_rules(
    access: TableAccessStore,
    connections: ConnectionStore,
    owner: Owner,
    admin: psycopg.Connection,
) -> None:
    mine = saved_id(connections, owner)
    access.set(owner.workspace_id, mine, "viewer", ["orders"])

    connections.delete(owner.workspace_id, mine)

    row = admin.execute(
        "SELECT count(*) FROM querymind_app.table_access WHERE connection_id = %s", (mine,)
    ).fetchone()
    assert row is not None and row[0] == 0


def client_as(
    settings: Settings, access: TableAccessStore, owner: Owner, role: str, *, refused: bool
) -> tuple[TestClient, FakeLLMProvider]:
    draft = SqlDraft(sql="SELECT count(*) FROM customers", explanation="Count.", tables_used=[])
    # Refused: all three tries are. Else one try, then the answer's summary (a text call).
    llm = FakeLLMProvider([draft, draft, draft] if refused else [draft, "91 customers."])
    # Retrieval off: this test is about permissions, not the schema index.
    own = settings.model_copy(update={"schema_retrieval": "off"})
    pipeline = Pipeline(llm=llm, settings=own)
    app = create_app(
        pipeline=pipeline,
        explorer=create_explorer(own),
        store=create_store(own),
        table_access=access,
    )
    api = TestClient(signed_in(app, user_for(owner, role)), raise_server_exceptions=False)  # type: ignore[arg-type]
    return api, llm


QUESTION = {"question": "How many customers are there?"}


def test_done_when_a_viewer_cant_query_or_discover_a_hidden_table(
    settings: Settings, access: TableAccessStore, owner: Owner
) -> None:
    every = Pipeline(llm=FakeLLMProvider([]), settings=settings).table_names()
    access.set(owner.workspace_id, None, "viewer", [t for t in every if t != "customers"])

    api, llm = client_as(settings, access, owner, "viewer", refused=True)
    with api:
        viewer = api.post("/ask", json=QUESTION)
        catalog = api.get("/catalog").json()
        semantic = api.get("/semantic").json()
        questions = api.get("/catalog/tables/customers/questions").json()
    admin_api, _ = client_as(settings, access, owner, "admin", refused=False)
    with admin_api:
        boss = admin_api.post("/ask", json=QUESTION)

    assert (viewer.status_code, viewer.json()["error"]) == (422, "unsafe_sql")
    assert "Table customers is not allowed" in viewer.json()["message"]
    prompt = llm.calls[0].prompt
    assert "Table customers\n" not in prompt and "-> customers(" not in prompt
    assert "Table orders\n" in prompt  # the rest is still there
    assert "customers" not in [t["name"] for t in catalog["tables"]]
    assert len(catalog["tables"]) == len(every) - 1
    northwind = next(b for b in semantic["businesses"] if b["database"] == "northwind")
    assert "customers" not in [t["name"] for t in northwind["tables"]]
    assert all(m["table"] != "customers" for m in northwind["metrics"])
    assert questions == {"questions": []}
    assert boss.status_code == 200 and boss.json()["rows"] == [[91]]


def test_the_rule_is_only_for_this_workspace(
    settings: Settings, access: TableAccessStore, owner: Owner, other_owner: Owner
) -> None:
    access.set(owner.workspace_id, None, "viewer", ["orders"])

    api, _ = client_as(settings, access, other_owner, "viewer", refused=False)
    with api:
        theirs = api.post("/ask", json=QUESTION)

    assert theirs.status_code == 200 and theirs.json()["rows"] == [[91]]


def test_unused_connection_ids_are_fine(access: TableAccessStore, owner: Owner) -> None:
    # A rule lookup for a database with no rule (or no such id) is "every table", not an error.
    assert access.allowed(owner.workspace_id, UUID(int=1), "viewer") is None
