"""Row-level security (5.3): each user's scope reaches the database with every query.

Postgres does the filtering (its policies are tested on the real database in
tests/integration/test_row_scope_db.py). Here, no database: the scope's encoding and checks, the
executor setting it, the pipeline passing it from both `run_sql` and `rerun`, a scoped user's prompt
without sample rows of row-secured tables, the routes looking it up fresh (and failing closed), the
admin routes, and the explorer's row counts.
"""

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.agent import pipeline as pipeline_module
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.api.explorer import ExplorerData
from querymind.api.main import create_app
from querymind.api.schemas import AskResponse, RerunResponse
from querymind.auth.tokens import AuthUser, Role
from querymind.config import Settings
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import Column, SchemaInfo, Table
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.row_scope import (
    RowScope,
    RowScopeInvalidError,
    checked,
    scope_value,
    without_row_samples,
)
from querymind.safety.sql_validator import validate_sql
from querymind.store.errors import StoreUnavailableError
from querymind.store.row_scope import RowScopeStore
from tests.auth_helpers import TEST_USER, signed_in
from tests.unit.test_api import RESULT
from tests.unit.test_api_conversations import ID as CONVERSATION
from tests.unit.test_api_conversations import FakePipeline as ConversationsPipeline
from tests.unit.test_api_conversations import FakeStore as ConversationsStore
from tests.unit.test_conversation import StubExecutor
from tests.unit.test_conversation import executor as executor  # fixture
from tests.unit.test_llm_keys import MemoryAudit

EASTERN: RowScope = ("Eastern",)
VIEWER = AuthUser(
    user_id=UUID("77777777-7777-4777-8777-777777777777"),
    workspace_id=TEST_USER.workspace_id,
    role="viewer",
    email="viewer@example.com",
)


# --- The scope -----------------------------------------------------------------------------------


def test_the_setting_is_a_star_for_every_row_else_the_tags() -> None:
    assert scope_value(None) == "*"
    assert scope_value(("Eastern", "Western")) == "Eastern,Western"
    assert scope_value(()) == ""  # no row at all: the policies match nothing


def test_tags_are_trimmed_kept_once_and_sorted() -> None:
    assert checked([" Western", "Eastern", "Western "]) == ("Eastern", "Western")


@pytest.mark.parametrize(
    "tags",
    [
        [],
        [""],
        ["  "],
        ["Eastern,Western"],
        ["*"],
        ["East*"],
        ["x" * 101],
        [str(i) for i in range(51)],
    ],
)
def test_a_scope_that_would_mean_something_else_is_refused(tags: list[str]) -> None:
    # "," is the separator and "*" means every row: either would widen the scope.
    with pytest.raises(RowScopeInvalidError):
        checked(tags)


ORDERS = Table(
    name="orders",
    columns=(Column(name="order_id", data_type="integer"),),
    sample_rows=((10248,),),
    row_security=True,
)
PRODUCTS = Table(
    name="products",
    columns=(Column(name="product_name", data_type="text"),),
    sample_rows=(("Chai",),),
)
SCHEMA = SchemaInfo(tables=(ORDERS, PRODUCTS))


def test_a_scoped_view_has_no_samples_of_row_secured_tables() -> None:
    seen = without_row_samples(SCHEMA)

    assert seen.tables[0].sample_rows == ()  # read with every row: could be another region's
    assert seen.tables[1] == PRODUCTS


# --- The executor --------------------------------------------------------------------------------


def fake_connection() -> MagicMock:
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.fetchmany.return_value = []
    cursor.description = []
    return conn


@pytest.mark.parametrize(
    ("scope", "value"), [(None, "*"), (("Eastern", "Western"), "Eastern,Western")]
)
def test_the_executor_sets_the_scope_in_the_querys_transaction(scope: RowScope, value: str) -> None:
    conn = fake_connection()
    query = validate_sql("SELECT count(*) FROM orders", allowed_tables={"orders"}, max_rows=10)

    execute_query(conn, query, timeout_seconds=5, row_scope=scope)

    # A parameter, never pasted into the SQL; `true` = this transaction only.
    assert call("SELECT set_config(%s, %s, true)", ("querymind.scope", value)) in (
        conn.execute.call_args_list
    )
    conn.transaction.assert_called_once()


# --- The pipeline --------------------------------------------------------------------------------

COUNT = SqlDraft(sql="SELECT count(*) FROM orders", explanation="Counts.", tables_used=["orders"])


def orders_pipeline(*responses: Any) -> Pipeline:
    settings = Settings.model_validate(
        {
            "postgres_host": "127.0.0.1",
            "postgres_port": 5433,
            "postgres_db": "northwind",
            "postgres_admin_user": "admin",
            "postgres_admin_password": SecretStr("x"),
            "analyst_ro_user": "analyst_ro",
            "analyst_ro_password": SecretStr("x"),
            "llm_provider": "groq",
            "groq_api_key": SecretStr("gsk_test"),
            "groq_model": "openai/gpt-oss-120b",
            "semantic_layer_paths": [],
        }
    )
    return Pipeline(llm=FakeLLMProvider(list(responses)), settings=settings, connect=MagicMock())


@pytest.fixture
def stub(executor: StubExecutor, monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    monkeypatch.setattr(pipeline_module, "read_schema", lambda conn: SCHEMA)
    monkeypatch.setattr(pipeline_module, "execute_query", executor)  # re-runs
    return executor


def test_the_scope_reaches_the_executor_from_a_question_and_a_rerun(stub: StubExecutor) -> None:
    pipeline = orders_pipeline(COUNT)

    pipeline.ask("How many orders?", row_scope=EASTERN, summarize_answer=False)
    pipeline.rerun("How many orders?", COUNT.sql, row_scope=EASTERN)
    pipeline.rerun("How many orders?", COUNT.sql)  # an admin

    assert stub.scopes == [EASTERN, EASTERN, None]


def test_a_scoped_users_prompt_has_no_sample_of_a_row_secured_table(stub: StubExecutor) -> None:
    scoped = orders_pipeline(COUNT)
    admin = orders_pipeline(COUNT)

    scoped.ask("How many orders?", row_scope=EASTERN, summarize_answer=False)
    admin.ask("How many orders?", summarize_answer=False)

    def prompt(pipeline: Pipeline) -> str:
        llm = pipeline._llm
        assert isinstance(llm, FakeLLMProvider)
        return llm.calls[0].prompt

    assert "10248" not in prompt(scoped) and "'Chai'" in prompt(scoped)
    assert "10248" in prompt(admin)


def test_views_are_cached_per_scoped_or_not(stub: StubExecutor) -> None:
    pipeline = orders_pipeline()

    scoped = pipeline._view(None, scoped=True)

    assert scoped is pipeline._view(None, scoped=True)
    assert scoped[0] is not pipeline._view(None)[0]


# --- The routes ----------------------------------------------------------------------------------


class Scopes:
    """Stands in for RowScopeStore."""

    def __init__(
        self,
        scope: RowScope = EASTERN,
        error: Exception | None = None,
        members: dict[UUID, tuple[Role, RowScope]] | None = None,
    ) -> None:
        self._scope = scope
        self.error = error
        self.members = members or {}
        self.lookups = 0

    def scope(self, workspace_id: UUID, user_id: UUID, role: Role) -> RowScope:
        if role == "admin":
            return None
        self.lookups += 1
        if self.error is not None:
            raise self.error
        return self._scope

    def member(self, workspace_id: UUID, user_id: UUID) -> tuple[Role, RowScope] | None:
        return self.members.get(user_id)

    def set(self, workspace_id: UUID, user_id: UUID, scope: RowScope) -> bool:
        if user_id not in self.members:
            return False
        self.members[user_id] = (self.members[user_id][0], scope)
        return True


class Recorder:
    """A pipeline that notes the row scope of each call."""

    def __init__(self) -> None:
        self.scopes: list[RowScope] = []

    def ask(self, question: str, **kw: Any) -> Any:
        self.scopes.append(kw["row_scope"])
        return RESULT

    def stream(self, question: str, **kw: Any) -> Any:
        from querymind.agent.events import Finished

        self.scopes.append(kw["row_scope"])
        yield Finished(RESULT)


def client(user: AuthUser, scopes: Any, **parts: Any) -> TestClient:
    parts.setdefault("pipeline", Recorder())
    app = signed_in(create_app(row_scopes=scopes, **parts), user)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_a_question_carries_the_users_scope_looked_up_fresh(path: str) -> None:
    pipeline, scopes = Recorder(), Scopes()
    with client(VIEWER, scopes, pipeline=pipeline) as api:
        for _ in range(2):
            assert api.post(path, json={"question": "Orders?"}).status_code == 200
    with client(TEST_USER, scopes, pipeline=pipeline) as api:  # an admin
        assert api.post(path, json={"question": "Orders?"}).status_code == 200

    assert pipeline.scopes == [EASTERN, EASTERN, None]
    assert scopes.lookups == 2  # once per question (not from the token); never for the admin


def test_a_scope_that_cant_be_read_refuses_the_question_never_every_row() -> None:
    pipeline = Recorder()
    down = Scopes(error=StoreUnavailableError("Row permissions can't be read right now."))
    with client(VIEWER, down, pipeline=pipeline) as api:
        response = api.post("/ask", json={"question": "Orders?"})

    assert response.status_code == 503
    assert pipeline.scopes == []


def test_a_rerun_carries_the_users_scope() -> None:
    class Rerunner(ConversationsPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.scopes: list[RowScope] = []

        def rerun(self, question: str, sql: str, **kw: Any) -> QueryResult:
            self.scopes.append(kw["row_scope"])
            return RESULT.result

    pipeline = Rerunner()
    with client(VIEWER, Scopes(), pipeline=pipeline, store=ConversationsStore()) as api:
        assert api.post(f"/conversations/{CONVERSATION}/turns/1/rerun").status_code == 200

    assert pipeline.scopes == [EASTERN]


def _from_the_store(keyword: ast.keyword) -> bool:
    """`row_scope=row_scope_of(...)`: the user's scope, looked up; never a constant."""
    value = keyword.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "row_scope_of"
    )


def test_every_pipeline_call_in_the_api_passes_the_users_scope() -> None:
    """A call that forgot `row_scope` would show a regional manager every region's rows."""
    calls, missing = 0, []
    for path in Path("src/querymind/api").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"ask", "stream", "rerun"}
            ):
                calls += 1
                if not any(k.arg == "row_scope" and _from_the_store(k) for k in node.keywords):
                    missing.append(f"{path.name}:{node.lineno} .{node.func.attr}()")

    assert calls >= 3 and missing == []


# --- The admin routes ----------------------------------------------------------------------------


def admin_client(scopes: Scopes, audit: MemoryAudit, user: AuthUser = TEST_USER) -> TestClient:
    return client(user, scopes, audit=audit)


def test_an_admin_sets_and_lifts_a_members_scope_audited() -> None:
    scopes, audit = Scopes(members={VIEWER.user_id: ("viewer", None)}), MemoryAudit()
    url = f"/admin/members/{VIEWER.user_id}/row-scope"
    with admin_client(scopes, audit) as api:
        before = api.get(url).json()
        set_ = api.put(url, json={"row_scope": ["Western", " Eastern"]}).json()
        lifted = api.put(url, json={"row_scope": None}).json()

    assert before == {"user_id": str(VIEWER.user_id), "role": "viewer", "row_scope": None}
    assert set_["row_scope"] == ["Eastern", "Western"]
    assert lifted["row_scope"] is None and scopes.members[VIEWER.user_id] == ("viewer", None)
    assert [line.action for line in audit.lines] == ["row_scope_changed"] * 2
    assert audit.lines[0].detail == f"member {VIEWER.user_id}: Eastern, Western"


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"row_scope": []}, 422, "row_scope_invalid"),
        ({"row_scope": ["Eastern,Western"]}, 422, "row_scope_invalid"),
        ({"row_scope": ["*"]}, 422, "row_scope_invalid"),
        ({"scope": ["Eastern"]}, 422, "invalid_request"),  # a misspelt field isn't ignored
    ],
)
def test_a_scope_that_would_mean_something_else_is_a_422(
    body: dict[str, Any], status: int, error: str
) -> None:
    scopes = Scopes(members={VIEWER.user_id: ("viewer", None)})
    with admin_client(scopes, MemoryAudit()) as api:
        response = api.put(f"/admin/members/{VIEWER.user_id}/row-scope", json=body)

    assert (response.status_code, response.json()["error"]) == (status, error)
    assert scopes.members[VIEWER.user_id] == ("viewer", None)


def test_an_admins_scope_cant_be_set_and_a_stranger_is_a_404() -> None:
    scopes = Scopes(members={TEST_USER.user_id: ("admin", None)})
    with admin_client(scopes, MemoryAudit()) as api:
        own = api.put(f"/admin/members/{TEST_USER.user_id}/row-scope", json={"row_scope": ["X"]})
        stranger = api.get(f"/admin/members/{uuid4()}/row-scope")

    assert (own.status_code, own.json()["error"]) == (422, "row_scope_invalid")
    assert (stranger.status_code, stranger.json()["error"]) == (404, "member_not_found")


def test_only_an_admin_manages_scopes() -> None:
    scopes = Scopes(members={VIEWER.user_id: ("viewer", None)})
    with admin_client(scopes, MemoryAudit(), user=VIEWER) as api:
        response = api.put(f"/admin/members/{VIEWER.user_id}/row-scope", json={"row_scope": None})

    assert response.status_code == 403


# --- The store and the explorer ------------------------------------------------------------------


def test_the_store_never_asks_for_an_admin_and_a_former_member_sees_no_row() -> None:
    store = RowScopeStore(MagicMock(), connect=MagicMock(side_effect=AssertionError("no lookup")))
    assert store.scope(uuid4(), uuid4(), "admin") is None

    gone = RowScopeStore(MagicMock())
    gone.member = lambda workspace_id, user_id: None  # type: ignore[method-assign]
    assert gone.scope(uuid4(), uuid4(), "viewer") == ()  # (): no row, not every row


def test_a_scoped_user_sees_no_row_count_of_a_row_secured_table() -> None:
    explorer = ExplorerData([], lambda: (SCHEMA, {"orders": 830, "products": 77}))

    counts = {t.name: t.row_count for t in explorer.visible_catalog(None, scoped=True).tables}
    everyone = {t.name: t.row_count for t in explorer.visible_catalog(None).tables}

    assert counts == {"orders": None, "products": 77}  # 830 would tell about the other regions
    assert everyone == {"orders": 830, "products": 77}


def test_the_catalog_route_hides_them_for_a_scoped_viewer() -> None:
    explorer = ExplorerData([], lambda: (SCHEMA, {"orders": 830, "products": 77}))
    with client(VIEWER, Scopes(), explorer=explorer) as api:
        tables = api.get("/catalog").json()["tables"]

    assert {t["name"]: t["row_count"] for t in tables} == {"orders": None, "products": 77}


def test_an_answer_still_comes_back_whole_for_an_unscoped_viewer(stub: StubExecutor) -> None:
    reply = orders_pipeline(COUNT).ask("How many orders?", summarize_answer=False)
    assert isinstance(reply, AskResult)


# --- U7.2: the reply says which rows it was limited to -------------------------------------------

PRODUCT_COUNT = SqlDraft(
    sql="SELECT count(*) FROM products", explanation="Counts.", tables_used=["products"]
)


def test_a_reply_says_its_rows_were_limited_when_a_row_secured_table_was_read(
    stub: StubExecutor,
) -> None:
    reply = orders_pipeline(COUNT).ask(
        "How many orders?", row_scope=("Western", "Eastern"), summarize_answer=False
    )

    assert isinstance(reply, AskResult)
    assert reply.result.row_scope == ["Eastern", "Western"]


def test_a_reply_claims_no_limit_when_no_policy_filtered_it(stub: StubExecutor) -> None:
    # products has no row security: a scoped user sees every product row, so no chip.
    scoped = orders_pipeline(PRODUCT_COUNT).ask(
        "How many products?", row_scope=EASTERN, summarize_answer=False
    )
    unscoped = orders_pipeline(COUNT).ask("How many orders?", summarize_answer=False)

    assert isinstance(scoped, AskResult) and isinstance(unscoped, AskResult)
    assert scoped.result.row_scope is None
    assert unscoped.result.row_scope is None


def test_a_rerun_says_its_rows_were_limited_too(stub: StubExecutor) -> None:
    pipeline = orders_pipeline()

    orders = pipeline.rerun("How many orders?", COUNT.sql, row_scope=EASTERN)
    products = pipeline.rerun("How many products?", PRODUCT_COUNT.sql, row_scope=EASTERN)

    assert orders.row_scope == ["Eastern"]
    assert products.row_scope is None


def test_the_api_reply_and_the_rerun_reply_carry_it() -> None:
    limited = RESULT.model_copy(
        update={"result": RESULT.result.model_copy(update={"row_scope": ["Eastern"]})}
    )

    assert AskResponse.from_result(limited, conversation_id=CONVERSATION).row_scope == ["Eastern"]
    assert AskResponse.from_result(RESULT, conversation_id=CONVERSATION).row_scope is None
    assert "row_scope" in RerunResponse.model_fields
