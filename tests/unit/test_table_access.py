"""Table permissions (5.2): a restricted role can't query or discover the tables it may not use.

The views (safety/permissions.py), the pipeline's restricted runs, the routes passing each user's
tables (and failing closed), the explorer, and the admin routes. No database, no LLM.
"""

import ast
from collections.abc import Generator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

import querymind.semantic.retrieval as retrieval_module
from querymind.agent.events import AgentEvent, Finished
from querymind.agent.registry import PipelineRegistry
from querymind.agent.schemas import AgentReply, AskResult, FewShotExample, SqlDraft
from querymind.api.deps import get_current_user, get_table_access
from querymind.api.explorer import ExplorerData
from querymind.api.main import create_app
from querymind.auth.tokens import AuthUser
from querymind.db.executor import QueryResult
from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table
from querymind.llm import LLMProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.permissions import (
    NoPermittedTablesError,
    visible_catalog,
    visible_examples,
    visible_layers,
    visible_schema,
)
from querymind.semantic.catalog import build_catalog
from querymind.semantic.index import IndexHit
from querymind.semantic.loader import load_semantic_layers
from querymind.store.errors import StoreUnavailableError
from tests.auth_helpers import TEST_USER, signed_in
from tests.unit.test_api import RESULT
from tests.unit.test_api_connections import MemoryConnections
from tests.unit.test_api_conversations import ID as CONVERSATION
from tests.unit.test_api_conversations import FakePipeline as ConversationsPipeline
from tests.unit.test_api_conversations import FakeStore as ConversationsStore
from tests.unit.test_api_explorer import LAYERS, FakeDatabase
from tests.unit.test_conversation import StubExecutor, pipeline_with
from tests.unit.test_conversation import executor as executor  # fixture
from tests.unit.test_llm_keys import MemoryAudit
from tests.unit.test_onboarding import STORED, Routes
from tests.unit.test_registry import SPEC
from tests.unit.test_saved_index import saved_pipeline

NORTHWIND = load_semantic_layers([Path("semantic_layer/northwind.yaml")])
VIEWER = AuthUser(
    user_id=UUID("55555555-5555-4555-8555-555555555555"),
    workspace_id=TEST_USER.workspace_id,
    role="viewer",
    email="viewer@example.com",
)
ORDERS_ONLY = frozenset({"orders"})
ROWS = QueryResult(columns=["n"], rows=[[1]], row_count=1, truncated=False, duration_ms=1)


def keyed(name: str, *targets: str) -> Table:
    keys = tuple(
        ForeignKey(columns=(f"{t}_id",), ref_table=t, ref_columns=("id",)) for t in targets
    )
    return Table(name=name, columns=(Column(name="id", data_type="integer"),), foreign_keys=keys)


# --- The views ------------------------------------------------------------------------------------


def test_a_hidden_table_and_every_key_pointing_at_it_are_gone() -> None:
    schema = SchemaInfo(tables=(keyed("customers"), keyed("orders", "customers", "shippers")))

    seen = visible_schema(schema, {"orders", "shippers"})

    assert [t.name for t in seen.tables] == ["orders"]
    assert [fk.ref_table for fk in seen.tables[0].foreign_keys] == ["shippers"]


def test_a_term_whose_sql_reads_a_hidden_table_is_gone_with_its_joins() -> None:
    (layer,) = visible_layers(NORTHWIND, {"customers", "orders"})

    # active_customer reads customers + orders; key_account also sums order_details (hidden).
    assert "active_customer" in layer.metrics and "key_account" not in layer.metrics
    assert "revenue" not in layer.metrics  # on order_details itself
    assert set(layer.table_scope) == {"customers", "orders"}
    assert all(set(join.tables) <= {"customers", "orders"} for join in layer.joins)
    assert layer.joins  # orders ↔ customers stays


def test_a_business_with_no_visible_table_is_dropped() -> None:
    assert visible_layers(NORTHWIND, {"invoice"}) == []


def test_examples_reading_a_hidden_table_are_dropped() -> None:
    examples = [
        FewShotExample(id="a", question="Orders?", sql="SELECT count(*) FROM orders"),
        FewShotExample(
            id="b",
            question="Orders per customer?",
            sql="SELECT c.company_name FROM customers c JOIN orders o USING (customer_id)",
        ),
    ]

    assert [e.id for e in visible_examples(examples, ORDERS_ONLY)] == ["a"]


def test_the_explorer_catalog_keeps_no_key_to_a_hidden_table() -> None:
    schema = SchemaInfo(tables=(keyed("customers"), keyed("orders", "customers")))
    catalog = build_catalog(schema, {}, [])

    seen = visible_catalog(catalog, ORDERS_ONLY)

    assert [t.name for t in seen.tables] == ["orders"]
    assert seen.tables[0].foreign_keys == []


# --- The pipeline ---------------------------------------------------------------------------------

ON_CUSTOMERS = SqlDraft(sql="SELECT count(*) FROM customers", explanation="", tables_used=[])
ON_ORDERS = SqlDraft(sql="SELECT count(*) FROM orders", explanation="", tables_used=[])


def test_a_restricted_run_shows_and_allows_only_its_tables(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(ON_CUSTOMERS, ON_CUSTOMERS, ON_CUSTOMERS)

    with pytest.raises(UnsafeSqlError, match="customers is not allowed"):
        pipeline.ask("How many customers?", allowed_tables=ORDERS_ONLY, summarize_answer=False)

    assert executor.queries == []  # refused every time, never run
    # The first prompt's schema has orders only (repairs quote the LLM's own refused SQL back).
    first = llm.calls[0].prompt.replace("How many customers?", "")
    assert "orders" in first and "customers" not in first


def test_the_same_pipeline_still_answers_everything_for_an_unrestricted_user(
    executor: StubExecutor,
) -> None:
    pipeline, _ = pipeline_with(ON_CUSTOMERS)

    reply = pipeline.ask("How many customers?", allowed_tables=None, summarize_answer=False)

    assert isinstance(reply, AskResult)


def test_no_allowed_table_is_refused_before_any_llm_call(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(ON_ORDERS)

    with pytest.raises(NoPermittedTablesError):
        pipeline.ask("How many orders?", allowed_tables=frozenset({"salaries"}))

    assert llm.calls == []


def test_a_rerun_cant_reach_a_table_hidden_since(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with()

    with pytest.raises(UnsafeSqlError):
        pipeline.rerun("q", "SELECT count(*) FROM customers", allowed_tables=ORDERS_ONLY)
    assert pipeline.rerun("q", "SELECT count(*) FROM orders", allowed_tables=ORDERS_ONLY)


def test_views_are_cached_by_the_tables_really_visible(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with()

    first = pipeline._view(frozenset({"orders", "gone_since"}))
    second = pipeline._view(ORDERS_ONLY)

    assert first is second
    assert pipeline._view(frozenset({"orders", "customers"})) == pipeline._schema_info()


def test_retrieval_never_brings_back_a_hidden_table(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, _, _ = saved_pipeline(monkeypatch)  # 40 tables, keyword search
    hits = [IndexHit(id=f"table:{t}", kind="table", table=t, score=1.0) for t in ("salaries",)]
    hits += [IndexHit(id="table:invoices", kind="table", table="invoices", score=0.5)]
    monkeypatch.setattr(retrieval_module, "keyword_search", lambda *a, **k: hits)
    allowed = frozenset(n for n in pipeline._schema_info()[0].schema.table_names if n != "salaries")

    _, retriever = pipeline._view(allowed)
    assert retriever is not None
    selection = retriever.select("Total salaries per department")

    assert "salaries" not in selection.info.tables
    assert selection.info.tables == ("invoices",)
    assert (
        "salaries" not in selection.schema_text and "salaries" not in selection.repair_schema_text
    )


def test_a_few_visible_tables_are_sent_whole(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline, _, _ = saved_pipeline(monkeypatch)  # 40 tables: retrieval is on for everyone

    catalog, retriever = pipeline._view(frozenset({"invoices", "customers"}))

    assert retriever is None  # 2 tables: no search, no recall risk
    assert set(catalog.schema.table_names) == {"invoices", "customers"}


# --- The routes -----------------------------------------------------------------------------------


class Rules:
    """Stands in for TableAccessStore."""

    def __init__(
        self, allowed: frozenset[str] | None = None, error: Exception | None = None
    ) -> None:
        self._allowed = allowed
        self.error = error
        self.saved: list[tuple[UUID | None, str, list[str] | None]] = []

    def allowed(
        self, workspace_id: UUID, connection_id: UUID | None, role: str
    ) -> frozenset[str] | None:
        if self.error is not None:
            raise self.error
        return None if role == "admin" else self._allowed

    def rules(self, workspace_id: UUID, connection_id: UUID | None) -> dict[str, Any]:
        found = {role: tables for _, role, tables in self.saved}
        return {"analyst": found.get("analyst"), "viewer": found.get("viewer")}

    def set(self, workspace_id: UUID, connection_id: UUID | None, role: str, tables: Any) -> None:
        self.saved.append((connection_id, role, None if tables is None else sorted(tables)))


class Recorder:
    """A pipeline that notes the tables each call was allowed."""

    def __init__(self) -> None:
        self.allowed: list[frozenset[str] | None] = []

    def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        llm: LLMProvider | None = None,
        allowed_tables: frozenset[str] | None = None,
    ) -> AgentReply:
        self.allowed.append(allowed_tables)
        return RESULT

    def stream(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        llm: LLMProvider | None = None,
        allowed_tables: frozenset[str] | None = None,
    ) -> Generator[AgentEvent | Finished]:
        self.allowed.append(allowed_tables)
        yield Finished(RESULT)

    def table_names(self) -> list[str]:
        return ["customers", "orders"]


def app_with(rules: Rules, user: AuthUser = VIEWER, **parts: Any) -> TestClient:
    pipeline = parts.pop("pipeline", Recorder())
    app = signed_in(create_app(pipeline=pipeline, table_access=rules, **parts), user)  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_a_question_carries_the_users_tables(path: str) -> None:
    pipeline = Recorder()
    with app_with(Rules(ORDERS_ONLY), pipeline=pipeline) as api:
        assert api.post(path, json={"question": "How many orders?"}).status_code == 200
    with app_with(Rules(ORDERS_ONLY), TEST_USER, pipeline=pipeline) as api:  # an admin
        assert api.post(path, json={"question": "How many orders?"}).status_code == 200

    assert pipeline.allowed == [ORDERS_ONLY, None]


def test_rules_that_cant_be_read_refuse_the_question_not_open_everything() -> None:
    pipeline = Recorder()
    down = Rules(error=StoreUnavailableError("Table permissions can't be read right now."))
    with app_with(down, pipeline=pipeline) as api:
        response = api.post("/ask", json={"question": "How many orders?"})

    assert response.status_code == 503
    assert pipeline.allowed == []  # the pipeline was never asked


def test_a_role_with_no_table_gets_a_clear_403() -> None:
    class NoTables(Recorder):
        def ask(self, question: str, **kw: Any) -> AgentReply:
            raise NoPermittedTablesError("You can't use any table of this database.")

    with app_with(Rules(frozenset()), pipeline=NoTables()) as api:
        response = api.post("/ask", json={"question": "q"})

    assert (response.status_code, response.json()["error"]) == (403, "no_tables_permitted")


def _from_the_rules(keyword: ast.keyword) -> bool:
    """`allowed_tables=allowed_tables(...)`: the user's tables, looked up: never None."""
    value = keyword.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "allowed_tables"
    )


def test_every_pipeline_call_in_the_api_passes_the_users_tables() -> None:
    """A call that forgot `allowed_tables` (or passed None) would answer from every table."""
    missing = []
    for path in Path("src/querymind/api").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"ask", "stream", "rerun"}
                and not any(k.arg == "allowed_tables" and _from_the_rules(k) for k in node.keywords)
            ):
                missing.append(f"{path.name}:{node.lineno} .{node.func.attr}()")

    assert missing == []


def test_a_rerun_carries_the_users_tables() -> None:
    class Rerunner(ConversationsPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.allowed: list[frozenset[str] | None] = []

        def rerun(self, question: str, sql: str, **kw: Any) -> QueryResult:
            self.allowed.append(kw.get("allowed_tables"))
            return ROWS

    pipeline = Rerunner()
    app = create_app(pipeline=pipeline, store=ConversationsStore(), table_access=Rules(ORDERS_ONLY))  # type: ignore[arg-type]
    with TestClient(signed_in(app, VIEWER)) as api:
        assert api.post(f"/conversations/{CONVERSATION}/turns/1/rerun").status_code == 200

    assert pipeline.allowed == [ORDERS_ONLY]


def test_a_saved_databases_dictionary_names_only_the_viewers_tables() -> None:
    routes = Routes()
    record = routes.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
    routes.store.status, routes.store.dictionary, routes.store.version = "ready", STORED, 1
    routes.app.dependency_overrides[get_current_user] = lambda: VIEWER
    routes.app.dependency_overrides[get_table_access] = lambda: Rules(ORDERS_ONLY)
    with TestClient(routes.app) as client:
        dictionary = client.get(f"/connections/{record.id}/dictionary").json()
        status = client.get(f"/connections/{record.id}/onboarding")

    assert set(STORED.tables) > {"orders"}  # there was more to hide
    assert set(dictionary["tables"]) == {"orders"}
    assert status.status_code == 403  # its counts are the whole database's


# --- The explorer ---------------------------------------------------------------------------------


def explorer_app(rules: Rules, store: Any = None) -> TestClient:
    explorer = ExplorerData(LAYERS, FakeDatabase())
    return app_with(rules, explorer=explorer, store=store)


def test_the_explorer_shows_a_viewer_only_its_tables() -> None:
    with explorer_app(Rules(ORDERS_ONLY)) as api:
        catalog = api.get("/catalog")
        semantic = api.get("/semantic").json()

    assert catalog.json()["tables"] == []  # FakeDatabase has only customers
    (business,) = semantic["businesses"]  # chinook has no visible table: gone
    assert [t["name"] for t in business["tables"]] == ["orders"]
    # The structure names only orders (free text is shown as written: safety/permissions.py).
    assert {m["table"] for m in business["metrics"]} == {"orders"}
    assert all("customers" not in m["sql"] for m in business["metrics"])
    assert all("customers" not in j["left"] + j["right"] for j in business["joins"])


def test_hidden_terms_and_examples_are_dropped_quietly(caplog: pytest.LogCaptureFixture) -> None:
    with explorer_app(Rules(ORDERS_ONLY)) as api:
        api.get("/semantic")

    assert "unsafe sql rejected" not in caplog.text


def test_a_hidden_tables_questions_look_like_a_missing_tables() -> None:
    class Questions:
        def recent_questions_using(self, table: str, **kw: Any) -> list[Any]:
            raise AssertionError("never looked up for a hidden table")

    with explorer_app(Rules(ORDERS_ONLY), store=Questions()) as api:
        hidden = api.get("/catalog/tables/customers/questions")

    assert (hidden.status_code, hidden.json()) == (200, {"questions": []})


# --- The admin routes -----------------------------------------------------------------------------


def admin_app(rules: Rules, user: AuthUser = TEST_USER, audit: Any = None) -> TestClient:
    demo = Recorder()
    connections = MemoryConnections()
    registry = PipelineRegistry(
        demo,  # type: ignore[arg-type]
        connections,  # type: ignore[arg-type]
        settings=pipeline_with()[0]._settings,
        build=lambda record, password, dictionary: demo,  # type: ignore[arg-type,return-value]
    )
    admin_app.connections = connections  # type: ignore[attr-defined]
    return app_with(rules, user, pipeline=demo, pipelines=registry, audit=audit)


def test_an_admin_sets_a_rule_and_it_is_audited() -> None:
    rules, audit = Rules(), MemoryAudit()
    with admin_app(rules, audit=audit) as api:
        put = api.put("/table-access", json={"role": "viewer", "tables": ["orders"]})
        got = api.get("/table-access").json()

    assert put.status_code == 200, put.text
    assert rules.saved == [(None, "viewer", ["orders"])]
    assert got == {
        "connection_id": None,
        "tables": ["customers", "orders"],
        "analyst": None,
        "viewer": ["orders"],
    }
    assert [(e.action, e.detail) for e in audit.lines] == [
        ("table_access_changed", "viewer: 1 tables")
    ]


def test_a_rule_naming_a_missing_table_is_refused() -> None:
    rules = Rules()
    with admin_app(rules) as api:
        response = api.put("/table-access", json={"role": "viewer", "tables": ["ordres"]})

    assert (response.status_code, response.json()["error"]) == (422, "table_access_invalid")
    assert rules.saved == []


def test_only_an_admin_sees_or_sets_rules() -> None:
    with admin_app(Rules(), VIEWER) as api:
        assert api.get("/table-access").status_code == 403
        assert api.put("/table-access", json={"role": "viewer", "tables": None}).status_code == 403


def test_another_workspaces_database_is_a_404() -> None:
    rules = Rules()
    with admin_app(rules) as api:
        theirs = admin_app.connections.save(uuid4(), "Theirs", SPEC, SecretStr("pw"))  # type: ignore[attr-defined]
        response = api.put(
            "/table-access",
            json={"connection_id": str(theirs.id), "role": "viewer", "tables": []},
        )

    assert response.status_code == 404
    assert rules.saved == []
