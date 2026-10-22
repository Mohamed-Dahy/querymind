"""Every golden reference SQL is safe, runs on Northwind, and gives the known right answer.

Task 1.8's "done when": the file exists and every reference SQL runs correctly. A wrong
reference would make the eval punish correct answers, so the references get tests too.
"""

import psycopg
import pytest
import sqlglot

from evals.golden_set import GOLDEN_DIR, GoldenQuestion, load_golden_set
from querymind.agent.sanity import check_result
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import introspect_schema
from querymind.safety.sql_validator import validate_sql

pytestmark = pytest.mark.integration

GOLDEN = load_golden_set(GOLDEN_DIR / "northwind.yaml")
BUSINESS = load_golden_set(GOLDEN_DIR / "northwind_business.yaml")  # business terms (3.1)
ALL_QUESTIONS = GOLDEN.questions + BUSINESS.questions
BY_ID = {q.id: q for q in ALL_QUESTIONS}


@pytest.fixture(scope="module")
def allowed_tables(settings: Settings) -> frozenset[str]:
    with connect_readonly(settings) as conn:
        return introspect_schema(conn).table_names


def run(conn: psycopg.Connection, item: GoldenQuestion, tables: frozenset[str]) -> QueryResult:
    # The same gate the agent's SQL goes through: references must be valid, safe queries too.
    query = validate_sql(item.sql, allowed_tables=tables, max_rows=1000)
    return execute_query(conn, query, timeout_seconds=10)


@pytest.mark.parametrize("item", ALL_QUESTIONS, ids=lambda q: q.id)
def test_reference_sql_passes_validation_and_runs(
    ro_conn: psycopg.Connection, allowed_tables: frozenset[str], item: GoldenQuestion
) -> None:
    result = run(ro_conn, item, allowed_tables)
    assert result.row_count >= 1, "a question whose answer is empty can't tell right from wrong"
    assert not result.truncated, "the whole answer must fit under the row limit"


@pytest.mark.parametrize("item", [q for q in ALL_QUESTIONS if q.ordered], ids=lambda q: q.id)
def test_ordered_questions_sort_their_result(item: GoldenQuestion) -> None:
    tree = sqlglot.parse_one(item.sql, read="postgres")
    assert tree.args.get("order") is not None, "ordered: true needs an ORDER BY on the outer query"


@pytest.mark.parametrize(
    ("question_id", "expected"),
    [
        ("e01_customer_count", [[91]]),
        ("e07_orders_1997", [[408]]),
        ("m04_busiest_employee", [["Margaret", "Peacock", 156]]),
        ("m08_late_shipments", [[37]]),
        # Business terms (3.1), by the company rules in semantic_layer/northwind.yaml
        ("b01_key_account_count", [[7]]),
        ("b03_late_shipments_1997", [[22]]),
        ("b06_active_customers", [[71]]),
        ("b04_carrier_most_late", [["United Package", 16]]),
    ],
)
def test_known_northwind_facts(
    ro_conn: psycopg.Connection, allowed_tables: frozenset[str], question_id: str, expected: object
) -> None:
    assert run(ro_conn, BY_ID[question_id], allowed_tables).rows == expected


def test_known_revenue_figures(ro_conn: psycopg.Connection, allowed_tables: frozenset[str]) -> None:
    # Classic Northwind totals (revenue = unit_price * quantity * (1 - discount)).
    [[revenue_1997]] = run(ro_conn, BY_ID["m07_revenue_1997"], allowed_tables).rows
    assert revenue_1997 == pytest.approx(617_085.20, abs=0.01)
    top = run(ro_conn, BY_ID["h02_top3_customers_revenue"], allowed_tables).rows
    assert top[0][0] == "QUICK-Stop"
    assert top[0][1] == pytest.approx(110_277.31, abs=0.01)


def test_top_n_answers_have_no_ties_at_the_cutoff(
    ro_conn: psycopg.Connection, allowed_tables: frozenset[str]
) -> None:
    # If the Nth and (N+1)th values tied, two different "top N" lists would both be right.
    for question_id, n, value_column in [
        ("e06_top5_expensive", 5, "unit_price"),
        ("h01_top5_products_revenue", 5, "revenue"),
        ("h02_top3_customers_revenue", 3, "revenue"),
        ("b04_carrier_most_late", 1, "count"),
        ("b08_top_clients_turnover_1997", 3, "turnover"),
        ("b12_reps_for_key_accounts", 3, "key_account_orders"),
        ("b13_active_customers_by_country", 3, "active_customers"),
    ]:
        sql = BY_ID[question_id].sql.replace(f"LIMIT {n}", f"LIMIT {n + 1}")
        if value_column == "unit_price":  # e06 only returns names; fetch the prices to compare
            sql = sql.replace("SELECT product_name", "SELECT product_name, unit_price")
        query = validate_sql(sql, allowed_tables=allowed_tables, max_rows=100)
        rows = execute_query(ro_conn, query, timeout_seconds=10).rows
        assert rows[n - 1][-1] != rows[n][-1], question_id


@pytest.mark.parametrize("item", ALL_QUESTIONS, ids=lambda q: q.id)
def test_right_answers_raise_no_sanity_warnings(
    ro_conn: psycopg.Connection, allowed_tables: frozenset[str], item: GoldenQuestion
) -> None:
    # The sanity check (2.3) must not cry wolf: these results are known to be right.
    warnings = check_result(run(ro_conn, item, allowed_tables))
    assert [w.code for w in warnings] == []
