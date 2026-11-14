"""PII masking (5.4): personal data is masked for every role but admin, and can't be fished out.

The guard's checks (the attack cases: filters, sorts, functions, set operations, subqueries,
whole rows...), the masking itself, what a masked user's prompt shows, the pipeline end to end
(answers, summaries, re-runs), the routes passing each user's masking, and the dictionary's PII
flags. No database, no LLM.
"""

import ast
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.agent import pipeline as pipeline_module
from querymind.agent.pipeline import Pipeline
from querymind.agent.prompts.summarize import MASKED_NOTE
from querymind.agent.sanity import check_result
from querymind.agent.schemas import AnswerSource, AskResult, FewShotExample, SqlDraft
from querymind.api.main import create_app
from querymind.api.onboarding import DictionaryEdit, TableEdit, apply_edit
from querymind.auth.tokens import AuthUser
from querymind.config import Settings
from querymind.db.executor import QueryResult
from querymind.db.schema import Column, SchemaInfo, Table
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.pii import (
    HIDDEN,
    MASK,
    NO_MASK,
    MaskPlan,
    PiiGuard,
    hide_samples,
    likely_pii,
)
from querymind.safety.sql_validator import ValidatedQuery, validate_sql
from querymind.semantic.models import SemanticLayer
from querymind.semantic.render import PERSONAL_HEADER, schema_prompt_text
from querymind.semantic.retrieval import SchemaCatalog
from querymind.store.errors import DictionaryEditError
from tests.auth_helpers import TEST_USER, signed_in
from tests.unit.test_api import RESULT
from tests.unit.test_api_conversations import ID as CONVERSATION
from tests.unit.test_api_conversations import FakePipeline as ConversationsPipeline
from tests.unit.test_api_conversations import FakeStore as ConversationsStore
from tests.unit.test_api_stream import events_of
from tests.unit.test_conversation import StubExecutor
from tests.unit.test_conversation import executor as executor  # fixture
from tests.unit.test_onboarding import STORED


def table(name: str, *columns: str, rows: tuple[tuple[Any, ...], ...] = ()) -> Table:
    return Table(
        name=name,
        columns=tuple(Column(name=c, data_type="text") for c in columns),
        sample_rows=rows,
    )


SCHEMA = SchemaInfo(
    tables=(
        table(
            "customers",
            "customer_id",
            "company_name",
            "contact_name",
            "city",
            rows=(("ALFKI", "Alfreds Futterkiste", "Maria Anders", "Berlin"),),
        ),
        table("orders", "order_id", "customer_id", "ship_name", "freight"),
        table("products", "product_id", "product_name", "unit_price"),
    )
)
PII = frozenset({("customers", "contact_name"), ("orders", "ship_name")})
GUARD = PiiGuard(SCHEMA, PII)
VIEWER = AuthUser(
    user_id=UUID("66666666-6666-4666-8666-666666666666"),
    workspace_id=TEST_USER.workspace_id,
    role="viewer",
    email="viewer@example.com",
)


def checked(sql: str) -> ValidatedQuery:
    return validate_sql(sql, allowed_tables=SCHEMA.table_names, max_rows=100)


# --- The check: what a masked user's SQL may do with personal columns ----------------------------


@pytest.mark.parametrize(
    ("sql", "masked"),
    [
        ("SELECT company_name, contact_name FROM customers", {1}),
        ("SELECT c.contact_name AS name FROM customers AS c", {0}),
        ("SELECT * FROM customers", {2}),  # * is expanded, in Postgres's order
        ("SELECT * FROM customers JOIN orders USING (customer_id)", {2, 5}),
        ("SELECT count(contact_name), count(DISTINCT contact_name) FROM customers", set()),
        ("SELECT contact_name, count(*) FROM customers GROUP BY contact_name", {0}),
        ("SELECT city FROM customers GROUP BY city HAVING count(DISTINCT contact_name) > 1", set()),
        ("WITH x AS (SELECT contact_name AS n FROM customers) SELECT n FROM x", {0}),
        ("SELECT n FROM (SELECT contact_name FROM customers) AS t(n)", {0}),
        ("SELECT (SELECT contact_name FROM customers LIMIT 1) AS one", {0}),
        ("SELECT contact_name FROM customers UNION ALL SELECT ship_name FROM orders", {0}),
        ("SELECT company_name FROM customers WHERE city = 'Berlin' ORDER BY company_name", set()),
        (
            "SELECT company_name FROM customers WHERE customer_id IN "
            "(SELECT customer_id FROM orders WHERE freight > 10)",
            set(),
        ),
    ],
)
def test_personal_columns_selected_counted_or_grouped_are_allowed_and_masked(
    sql: str, masked: set[int]
) -> None:
    assert GUARD.plan(checked(sql)).masked == masked


@pytest.mark.parametrize(
    "sql",
    [
        # Yes/no questions: does a row come back?
        "SELECT company_name FROM customers WHERE contact_name = 'Maria Anders'",
        "SELECT company_name FROM customers WHERE contact_name LIKE 'M%'",
        "SELECT count(*) FILTER (WHERE contact_name = 'Maria Anders') FROM customers",
        "SELECT city FROM customers GROUP BY city HAVING max(contact_name) > 'M'",
        "SELECT company_name FROM customers WHERE company_name IN "
        "(SELECT contact_name FROM customers)",
        "SELECT company_name FROM customers c WHERE EXISTS "
        "(SELECT 1 FROM orders o WHERE o.ship_name = c.contact_name)",
        "SELECT o.order_id FROM orders o JOIN customers c ON c.contact_name = o.ship_name",
        # The order of rows tells.
        "SELECT company_name FROM customers ORDER BY contact_name",
        "SELECT contact_name FROM customers ORDER BY 1",
        "SELECT contact_name AS n FROM customers ORDER BY n",
        "SELECT DISTINCT ON (contact_name) contact_name FROM customers",
        "SELECT count(*) OVER (PARTITION BY contact_name) FROM customers",
        # A function or calculation: its result isn't masked, or its error quotes the value.
        "SELECT upper(contact_name) FROM customers",
        "SELECT length(contact_name) FROM customers",
        "SELECT contact_name::int FROM customers",
        "SELECT coalesce(contact_name, 'none') FROM customers",
        "SELECT max(contact_name) FROM customers",
        "SELECT string_agg(contact_name, ', ') FROM customers",
        "SELECT CASE WHEN contact_name LIKE 'M%' THEN 1 ELSE 0 END FROM customers",
        "SELECT (SELECT max(contact_name) FROM customers) AS m",
        # Through a CTE or a subquery, a personal column is still personal.
        "WITH x AS (SELECT contact_name AS n FROM customers) SELECT n FROM x WHERE n LIKE 'A%'",
        "SELECT n FROM (SELECT contact_name FROM customers) AS t(n) WHERE n = 'x'",
        "SELECT z FROM customers c, LATERAL (SELECT c.contact_name AS z) s WHERE z = 'a'",
        # Set operations other than UNION ALL compare values: the row count tells.
        "SELECT contact_name FROM customers UNION SELECT 'Maria Anders'",
        "SELECT company_name FROM customers INTERSECT SELECT ship_name FROM orders",
        "SELECT contact_name FROM customers EXCEPT SELECT 'Maria Anders'",
        # A whole row carries every column.
        "SELECT c FROM customers c",
    ],
)
def test_every_other_use_of_a_personal_column_is_refused(sql: str) -> None:
    with pytest.raises(UnsafeSqlError, match="personal data"):
        GUARD.plan(checked(sql))


def test_the_refusal_names_the_column_and_says_what_is_allowed() -> None:
    with pytest.raises(UnsafeSqlError) as caught:
        GUARD.plan(checked("SELECT 1 FROM customers WHERE contact_name = 'x'"))

    assert "customers.contact_name is personal data" in caught.value.reason
    assert "count it, or group by it" in caught.value.reason


def test_what_cant_be_checked_is_refused_not_trusted() -> None:
    # A recursive query, on a database with personal data in reach: fail closed.
    sql = (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
        "SELECT n, company_name FROM r, customers"
    )
    with pytest.raises(UnsafeSqlError, match="can't be checked"):
        GUARD.plan(checked(sql))


def test_a_query_without_personal_tables_skips_the_check() -> None:
    sql = (
        "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 3) "
        "SELECT n FROM r"
    )
    assert GUARD.plan(checked(sql)) is NO_MASK
    assert GUARD.plan(checked("SELECT product_name FROM products ORDER BY unit_price")) is NO_MASK


def test_flags_on_columns_that_dont_exist_are_ignored() -> None:
    guard = PiiGuard(SCHEMA, {("customers", "gone"), ("ghosts", "name")})
    assert guard.pii == frozenset()


# --- The masking ---------------------------------------------------------------------------------


def result(columns: list[str], *rows: list[Any]) -> QueryResult:
    return QueryResult(
        columns=columns, rows=list(rows), row_count=len(rows), truncated=False, duration_ms=1
    )


def test_masked_values_become_stars_and_null_stays_null() -> None:
    plan = MaskPlan(columns=("company_name", "contact_name"), masked=frozenset({1}))

    masked = plan.apply(result(["company_name", "contact_name"], ["Alfreds", "Maria"], ["X", None]))

    assert masked.rows == [["Alfreds", MASK], ["X", None]]
    assert masked.masked_columns == [1]


def test_a_result_whose_columns_dont_match_the_check_is_masked_whole() -> None:
    plan = MaskPlan(columns=("company_name", "contact_name"), masked=frozenset({1}))

    masked = plan.apply(result(["contact_name", "company_name"], ["Maria", "Alfreds"]))

    assert masked.rows == [[MASK, MASK]]
    assert masked.masked_columns == [0, 1]


def test_unnamed_expressions_dont_count_as_a_mismatch() -> None:
    # sqlglot calls count(*) `_col_1`, Postgres calls it `count`: positions still line up.
    plan = GUARD.plan(checked("SELECT contact_name, count(*) FROM customers GROUP BY contact_name"))

    masked = plan.apply(result(["contact_name", "count"], ["Maria", 1]))

    assert masked.rows == [[MASK, 1]] and masked.masked_columns == [0]


def test_masked_rows_arent_flagged_as_duplicates() -> None:
    # Two different names both show as ***: not a bad join.
    plan = MaskPlan(columns=("contact_name",), masked=frozenset({0}))
    masked = plan.apply(result(["contact_name"], ["Maria"], ["Ana"]))

    assert [w.code for w in check_result(masked)] == []


# --- What the LLM of a masked user sees ----------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    ["email", "home_phone", "first_name", "LastName", "ship_postal_code", "date_of_birth", "fax"],
)
def test_names_that_say_personal_data_are_guessed(column: str) -> None:
    assert likely_pii(column)


@pytest.mark.parametrize(
    "column", ["company_name", "product_name", "name", "city", "country", "order_date", "notes"]
)
def test_other_names_are_not(column: str) -> None:
    assert not likely_pii(column)


def test_personal_sample_values_are_hidden() -> None:
    hidden = hide_samples(SCHEMA, PII)

    assert hidden.tables[0].sample_rows == (("ALFKI", "Alfreds Futterkiste", HIDDEN, "Berlin"),)
    assert hidden.tables[1:] == SCHEMA.tables[1:]


def test_the_prompt_lists_the_masked_columns_only_when_there_are_some() -> None:
    masked = schema_prompt_text(SCHEMA, [], personal=PII)
    plain = schema_prompt_text(SCHEMA, [])

    assert PERSONAL_HEADER in masked
    assert "- customers: contact_name\n- orders: ship_name" in masked
    assert PERSONAL_HEADER not in plain
    assert PERSONAL_HEADER not in schema_prompt_text(SCHEMA, [], tables={"products"}, personal=PII)


def test_a_masked_catalog_drops_terms_and_examples_that_would_be_refused() -> None:
    layer = SemanticLayer.model_validate(
        {
            "database": "shop",
            "version": 1,
            "metrics": {
                "berlin_customer": {
                    "kind": "condition",
                    "table": "customers",
                    "sql": "customers.city = 'Berlin'",
                    "description": "In Berlin.",
                },
                "maria": {
                    "kind": "condition",
                    "table": "customers",
                    "sql": "customers.contact_name LIKE 'Maria%'",
                    "description": "Contact called Maria.",
                },
            },
        }
    )
    examples = [
        FewShotExample(id="ok", question="Contacts?", sql="SELECT contact_name FROM customers"),
        FewShotExample(
            id="filter",
            question="Is Maria a contact?",
            sql="SELECT count(*) FROM customers WHERE contact_name = 'Maria Anders'",
        ),
    ]

    seen = SchemaCatalog(SCHEMA, [layer], examples).masked(GUARD)

    assert list(seen.layers[0].metrics) == ["berlin_customer"]
    assert list(seen.examples) == ["ok"]
    assert "Maria Anders" not in seen.full_text and PERSONAL_HEADER in seen.full_text
    assert PERSONAL_HEADER in seen.text({"customers"})


# --- The pipeline --------------------------------------------------------------------------------

CONTACTS = SqlDraft(
    sql="SELECT company_name, contact_name FROM customers",
    explanation="Each customer's contact.",
    tables_used=["customers"],
)
BY_NAME = SqlDraft(
    sql="SELECT company_name FROM customers WHERE contact_name = 'Maria Anders'",
    explanation="The customer whose contact is Maria Anders.",
    tables_used=["customers"],
)
ROWS = result(["company_name", "contact_name"], ["Alfreds Futterkiste", "Maria Anders"])


def shop_pipeline(*responses: Any) -> tuple[Pipeline, FakeLLMProvider]:
    """A saved database (5.11) with no dictionary: its personal columns are guessed by name."""
    settings = Settings.model_validate(
        {
            "postgres_host": "127.0.0.1",
            "postgres_port": 5433,
            "postgres_db": "shop",
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
    llm = FakeLLMProvider(list(responses))
    source = AnswerSource(database="shop", description="")
    return Pipeline(llm=llm, settings=settings, connect=MagicMock(), source=source), llm


@pytest.fixture
def shop(executor: StubExecutor, monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    monkeypatch.setattr(pipeline_module, "read_schema", lambda conn: SCHEMA)
    monkeypatch.setattr(pipeline_module, "execute_query", executor)  # re-runs
    return executor


def test_a_masked_user_gets_masked_rows_and_a_summary_from_masked_rows(
    shop: StubExecutor,
) -> None:
    shop.replies.append(ROWS)
    pipeline, llm = shop_pipeline(CONTACTS, "Alfreds Futterkiste's contact is hidden.")

    reply = pipeline.ask("Who is each customer's contact?", mask_pii=True)

    assert isinstance(reply, AskResult)
    assert reply.result.rows == [["Alfreds Futterkiste", MASK]]
    assert reply.result.masked_columns == [1]
    sql_prompt, summary_prompt = (call.prompt for call in llm.calls)
    assert "Maria Anders" not in sql_prompt and PERSONAL_HEADER in sql_prompt
    assert "Maria Anders" not in summary_prompt and MASK in summary_prompt
    assert MASKED_NOTE in summary_prompt


def test_an_admin_on_the_same_pipeline_sees_everything(shop: StubExecutor) -> None:
    shop.replies.append(ROWS)
    pipeline, llm = shop_pipeline(CONTACTS, "Maria Anders.")

    reply = pipeline.ask("Who is each customer's contact?")

    assert isinstance(reply, AskResult)
    assert reply.result.rows == [["Alfreds Futterkiste", "Maria Anders"]]
    assert reply.result.masked_columns == []
    assert "Maria Anders" in llm.calls[0].prompt  # the sample row
    assert PERSONAL_HEADER not in llm.calls[0].prompt


def test_a_query_filtering_on_personal_data_is_repaired_never_run(shop: StubExecutor) -> None:
    shop.replies.append(ROWS)
    pipeline, llm = shop_pipeline(BY_NAME, CONTACTS)

    reply = pipeline.ask("Which customer has Maria Anders?", mask_pii=True, summarize_answer=False)

    assert isinstance(reply, AskResult)
    # Only the repaired query ran: the refused one never reached the database.
    assert [q.sql.split(" LIMIT")[0] for q in shop.queries] == [CONTACTS.sql]
    assert "customers.contact_name is personal data" in llm.calls[1].prompt  # the repair
    assert reply.failed_attempts[0].error_kind == "unsafe"


def test_a_rerun_is_checked_and_masked_too(shop: StubExecutor) -> None:
    shop.replies.extend([ROWS, ROWS])
    pipeline, _ = shop_pipeline()

    with pytest.raises(UnsafeSqlError, match="personal data"):
        pipeline.rerun("q", BY_NAME.sql, mask_pii=True)
    rerun = pipeline.rerun("q", CONTACTS.sql, mask_pii=True)

    assert rerun.rows == [["Alfreds Futterkiste", MASK]]
    assert pipeline.rerun("q", CONTACTS.sql).rows == ROWS.rows  # an admin


def test_views_are_cached_per_visible_tables_and_masking(shop: StubExecutor) -> None:
    pipeline, _ = shop_pipeline()

    masked = pipeline._view(None, mask_pii=True)

    assert masked is pipeline._view(None, mask_pii=True)
    assert masked[2] is not None and pipeline._view(None)[2] is None
    assert pipeline._view(frozenset({"customers"}), mask_pii=True)[2] is not masked[2]


# --- The routes ----------------------------------------------------------------------------------


class Recorder:
    """A pipeline that notes whether each call masked personal data."""

    def __init__(self) -> None:
        self.masked: list[bool] = []

    def ask(self, question: str, **kw: Any) -> Any:
        self.masked.append(kw["mask_pii"])
        return RESULT

    def stream(self, question: str, **kw: Any) -> Any:
        from querymind.agent.events import Finished

        self.masked.append(kw["mask_pii"])
        yield Finished(RESULT)


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_every_role_but_admin_is_masked(path: str) -> None:
    pipeline = Recorder()
    for user in (VIEWER, VIEWER.model_copy(update={"role": "analyst"}), TEST_USER):
        app = signed_in(create_app(pipeline=pipeline), user)
        with TestClient(app) as api:
            assert api.post(path, json={"question": "Contacts?"}).status_code == 200

    assert pipeline.masked == [True, True, False]


def test_the_reply_says_which_columns_are_masked() -> None:
    # What the web's U7.3 "masked" chip reads, in /ask, the stream's final event and a re-run.
    masked = MaskPlan(columns=("company_name", "contact_name"), masked=frozenset({1})).apply(ROWS)

    class Masked(Recorder):
        def ask(self, question: str, **kw: Any) -> Any:
            return RESULT.model_copy(update={"result": masked})

        def stream(self, question: str, **kw: Any) -> Any:
            from querymind.agent.events import Finished

            yield Finished(RESULT.model_copy(update={"result": masked}))

    with TestClient(signed_in(create_app(pipeline=Masked()), VIEWER)) as api:
        answer = api.post("/ask", json={"question": "Contacts?"}).json()
        stream = api.post("/ask/stream", json={"question": "Contacts?"})
    final = events_of(stream)[-1]

    assert answer["masked_columns"] == [1] and answer["rows"] == [["Alfreds Futterkiste", MASK]]
    assert final["reply"]["masked_columns"] == [1]


def test_a_rerun_is_masked_for_a_viewer() -> None:
    class Rerunner(ConversationsPipeline):
        def __init__(self) -> None:
            super().__init__()
            self.masked: list[bool] = []

        def rerun(self, question: str, sql: str, **kw: Any) -> QueryResult:
            self.masked.append(kw["mask_pii"])
            plan = MaskPlan(columns=tuple(ROWS.columns), masked=frozenset({1}))
            return plan.apply(ROWS) if kw["mask_pii"] else ROWS

    pipeline = Rerunner()
    app = create_app(pipeline=pipeline, store=ConversationsStore())  # type: ignore[arg-type]
    with TestClient(signed_in(app, VIEWER)) as api:
        rerun = api.post(f"/conversations/{CONVERSATION}/turns/1/rerun")

    assert pipeline.masked == [True]
    assert rerun.json()["masked_columns"] == [1]
    assert rerun.json()["rows"] == [["Alfreds Futterkiste", MASK]]


def _from_the_role(keyword: ast.keyword) -> bool:
    """`mask_pii=masks_pii(...)`: decided from the user's role, never a constant."""
    value = keyword.value
    return (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "masks_pii"
    )


def test_every_pipeline_call_in_the_api_decides_the_masking() -> None:
    """A call that forgot `mask_pii` would show a viewer every personal value."""
    calls, missing = 0, []
    for path in Path("src/querymind/api").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"ask", "stream", "rerun"}
            ):
                calls += 1
                if not any(k.arg == "mask_pii" and _from_the_role(k) for k in node.keywords):
                    missing.append(f"{path.name}:{node.lineno} .{node.func.attr}()")

    assert calls >= 3 and missing == []


# --- A saved database's PII flags ----------------------------------------------------------------


def test_an_admin_can_flag_and_unflag_personal_columns() -> None:
    edit = DictionaryEdit(
        tables={"customers": TableEdit(description="Clients.", pii=["company_name"])}
    )

    edited = apply_edit(STORED, edit)

    assert edited.tables["customers"].pii == ["company_name"]
    assert edited.tables["orders"].pii is None  # untouched: still guessed from names
    kept = apply_edit(edited, DictionaryEdit(tables={"customers": TableEdit(description="x")}))
    assert kept.tables["customers"].pii == ["company_name"]  # None = keep what it has


def test_a_flag_on_a_column_the_table_doesnt_have_is_refused() -> None:
    edit = DictionaryEdit(tables={"customers": TableEdit(pii=["password"])})

    with pytest.raises(DictionaryEditError, match="no column 'password'"):
        apply_edit(STORED, edit)
