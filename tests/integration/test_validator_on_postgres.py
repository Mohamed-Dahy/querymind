"""The validator's *output* is real, working PostgreSQL.

We run the SQL that sqlglot regenerates, not the LLM's original text, so the round trip
(parse → check → regenerate) must keep queries valid. sqlglot renames many functions
internally (date_trunc → TimestampTrunc, string_agg → GroupConcat, unnest → Explode), so each
query here is validated and then really run on Northwind as analyst_ro.
"""

import psycopg
import pytest

from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.schema import introspect_schema
from querymind.safety.sql_validator import validate_sql

pytestmark = pytest.mark.integration

MAX_ROWS = 50

ROUND_TRIP_QUERIES = [
    "SELECT count(*) FROM orders",
    "SELECT date_trunc('month', order_date) AS m, count(*) FROM orders GROUP BY 1 ORDER BY 1",
    "SELECT extract(year FROM order_date), date_part('month', order_date) FROM orders",
    "SELECT to_char(order_date, 'YYYY-MM'), to_date('1997-01-01', 'YYYY-MM-DD') FROM orders",
    "SELECT order_date + interval '1 month', age(shipped_date, order_date) FROM orders",
    "SELECT round(sum(unit_price * quantity * (1 - discount))::numeric, 2) FROM order_details",
    "SELECT string_agg(product_name, ', ' ORDER BY product_name) FROM products",
    "SELECT array_agg(product_id), array_length(array_agg(product_id), 1) FROM products",
    "SELECT unnest(ARRAY[1, 2, 3])",
    "SELECT n FROM generate_series(1, 12) AS g(n)",
    "SELECT count(*) FILTER (WHERE discount > 0) FROM order_details",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) FROM products",
    "SELECT product_name, dense_rank() OVER (ORDER BY unit_price DESC) FROM products",
    "SELECT employee_id, lag(order_date) OVER (PARTITION BY employee_id ORDER BY order_date) "
    "FROM orders",
    "SELECT coalesce(region, 'n/a'), nullif(country, ''), upper(city), length(city) FROM customers",
    "SELECT split_part(contact_name, ' ', 1), left(phone, 3), position('a' IN city) FROM customers",
    "SELECT CASE WHEN unit_price > 50 THEN 'premium' ELSE 'standard' END FROM products",
    "SELECT * FROM customers c WHERE EXISTS "
    "(SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)",
    "WITH RECURSIVE chain AS (SELECT employee_id, reports_to FROM employees "
    "UNION ALL SELECT e.employee_id, e.reports_to FROM employees e "
    "JOIN chain c ON e.reports_to = c.employee_id) SELECT count(*) FROM chain",
    "SELECT customer_id FROM orders UNION SELECT customer_id FROM customers",
    "SELECT * FROM orders FETCH FIRST 3 ROWS ONLY",
    "SELECT * FROM customers WHERE company_name ILIKE '%market%'",
    "SELECT $$; DROP TABLE orders$$ AS just_text",
]


@pytest.fixture(scope="module")
def allowed_tables(settings: Settings) -> frozenset[str]:
    """The real allowlist: whatever analyst_ro can see (introspection from step 1.1)."""
    with connect_readonly(settings) as conn:
        return introspect_schema(conn).table_names


@pytest.mark.parametrize("sql", ROUND_TRIP_QUERIES)
def test_validated_sql_runs_on_postgres(
    ro_conn: psycopg.Connection, allowed_tables: frozenset[str], sql: str
) -> None:
    query = validate_sql(sql, allowed_tables=allowed_tables, max_rows=MAX_ROWS)
    rows = ro_conn.execute(query.sql).fetchall()
    assert len(rows) <= MAX_ROWS + 1  # the validator allows one extra row (truncation check)
