"""The shipped semantic layer against real Northwind: names exist, every metric's SQL runs.

A definition that doesn't run would teach the LLM broken SQL, so each one goes through the same
gate as the agent's queries (validator, then the read-only role) and must return a row.
"""

from pathlib import Path

import psycopg
import pytest

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import SchemaInfo, introspect_schema
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.loader import check_against, load_semantic_layer
from querymind.semantic.models import Metric

pytestmark = pytest.mark.integration

LAYER = load_semantic_layer(Path(__file__).parents[2] / "semantic_layer" / "northwind.yaml")


@pytest.fixture(scope="module")
def schema(settings: Settings) -> SchemaInfo:
    with connect_readonly(settings) as conn:
        return introspect_schema(conn)


def run_metric(conn: psycopg.Connection, schema: SchemaInfo, metric: Metric) -> QueryResult:
    """A measure as `SELECT <expr> FROM <table>`, a condition as a count of matching rows."""
    if metric.kind == "measure":
        sql = f"SELECT {metric.sql} AS value FROM {metric.table}"
    else:
        sql = f"SELECT count(*) AS value FROM {metric.table} WHERE {metric.sql}"
    query = validate_sql(sql, allowed_tables=schema.table_names, max_rows=10)
    return execute_query(conn, query, timeout_seconds=10)


def test_the_layer_matches_the_database(schema: SchemaInfo) -> None:
    check_against(LAYER, schema)  # every table and column it names exists for analyst_ro


@pytest.mark.parametrize("name", sorted(LAYER.metrics))
def test_every_metric_runs(ro_conn: psycopg.Connection, schema: SchemaInfo, name: str) -> None:
    result = run_metric(ro_conn, schema, LAYER.metrics[name])
    assert result.row_count == 1
    [[value]] = result.rows
    assert isinstance(value, int | float) and value > 0, "a definition matching nothing is suspect"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("key_account", 7), ("active_customer", 71), ("late_shipment", 37), ("needs_restock", 2)],
)
def test_company_rules_give_the_known_counts(
    ro_conn: psycopg.Connection, schema: SchemaInfo, name: str, expected: int
) -> None:
    assert run_metric(ro_conn, schema, LAYER.metrics[name]).rows == [[expected]]


def test_the_llm_sees_the_definitions(settings: Settings) -> None:
    # The real pipeline on real Northwind: the SQL prompt holds the schema AND the definitions.
    draft = SqlDraft(sql="SELECT count(*) FROM customers", explanation="x", tables_used=[])
    llm = FakeLLMProvider([draft])
    pipeline = Pipeline(llm=llm, settings=settings)
    reply = pipeline.ask("How many key accounts do we have?", summarize_answer=False)
    assert isinstance(reply, AskResult)
    prompt = llm.calls[0].prompt
    assert "Table customers" in prompt
    assert "- key_account (also: key accounts" in prompt
    assert "HAVING sum(d.unit_price * d.quantity * (1 - d.discount)) >= 30000" in prompt
