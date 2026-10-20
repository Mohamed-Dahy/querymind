"""The SQL validator: real analyst queries pass, every attack is blocked, LIMIT is enforced.

Safety code needs attack tests (CLAUDE.md §10). Each rejected query below is something an LLM
could be tricked into writing, or a way to sneak past a naive check.
"""

import logging

import pytest
import sqlglot

from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import MAX_SQL_CHARS, ValidatedQuery, validate_sql

ALLOWED = frozenset({"orders", "customers", "products", "order_details", "employees"})
MAX_ROWS = 100


def validate(sql: str, max_rows: int = MAX_ROWS) -> ValidatedQuery:
    return validate_sql(sql, allowed_tables=ALLOWED, max_rows=max_rows)


def assert_rejected(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeSqlError, match=reason):
        validate(sql)


# --- Queries that must pass ----------------------------------------------------------------

GOOD_QUERIES = [
    "SELECT count(*) FROM customers",
    "sElEcT * FrOm Orders",  # unquoted names are case-insensitive in Postgres
    'SELECT * FROM "orders"',
    "SELECT * FROM public.orders",
    "SELECT * FROM orders;",  # one trailing semicolon is fine
    "SELECT * FROM orders -- the most recent ones",  # comments are fine (and dropped)
    "SELECT c.country, count(*) AS n FROM orders o JOIN customers c "
    "ON c.customer_id = o.customer_id GROUP BY c.country HAVING count(*) > 5 ORDER BY n DESC",
    "WITH totals AS (SELECT customer_id, sum(freight) AS f FROM orders GROUP BY customer_id) "
    "SELECT * FROM totals ORDER BY f DESC",
    "WITH a AS (SELECT 1 AS x), b AS (SELECT x FROM a) SELECT * FROM b",  # CTE uses earlier CTE
    "WITH RECURSIVE boss AS (SELECT employee_id, reports_to FROM employees "
    "UNION ALL SELECT e.employee_id, e.reports_to FROM employees e "
    "JOIN boss b ON e.reports_to = b.employee_id) SELECT * FROM boss",
    "SELECT customer_id FROM orders UNION SELECT customer_id FROM customers",
    "SELECT * FROM products WHERE category_id IN (SELECT category_id FROM products LIMIT 3)",
    "SELECT * FROM customers c WHERE EXISTS (SELECT 1 FROM orders o "
    "WHERE o.customer_id = c.customer_id)",
    "SELECT product_name, rank() OVER (ORDER BY unit_price DESC) FROM products",
    "SELECT employee_id, lag(order_date) OVER (PARTITION BY employee_id ORDER BY order_date) "
    "FROM orders",
    "SELECT date_trunc('month', order_date) AS m, round(sum(freight)::numeric, 2) FROM orders "
    "GROUP BY 1",
    "SELECT extract(year FROM order_date), to_char(order_date, 'YYYY-MM') FROM orders",
    "SELECT * FROM orders WHERE order_date >= DATE '1997-01-01' + interval '1 month'",
    "SELECT age(hire_date), make_date(1997, 1, 1) FROM employees",
    "SELECT CASE WHEN unit_price > 50 THEN 'premium' ELSE 'standard' END FROM products",
    "SELECT coalesce(region, 'n/a'), upper(country), length(city) FROM customers",
    "SELECT string_agg(product_name, ', ' ORDER BY product_name) FROM products",
    "SELECT count(*) FILTER (WHERE discount > 0) FROM order_details",
    "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY unit_price) FROM products",
    "SELECT * FROM customers WHERE company_name ILIKE '%market%'",
    "SELECT g.n FROM generate_series(1, 12) AS g(n)",  # a function as a table
    "SELECT '; DROP TABLE orders' AS just_text",  # dangerous-looking *text* is only a value
    "SELECT $$; DROP TABLE orders$$",  # same, with a dollar-quoted string
]


@pytest.mark.parametrize("sql", GOOD_QUERIES)
def test_real_analyst_queries_pass(sql: str) -> None:
    query = validate(sql)
    # The output is itself one valid SELECT.
    [reparsed] = sqlglot.parse(query.sql, read="postgres")
    assert reparsed is not None


def test_returns_real_tables_not_cte_names() -> None:
    query = validate(
        "WITH big AS (SELECT * FROM orders WHERE freight > 100) "
        "SELECT * FROM big JOIN customers USING (customer_id)"
    )
    assert query.tables == frozenset({"orders", "customers"})


def test_output_is_regenerated_without_comments() -> None:
    query = validate("SELECT order_id /* note */ FROM orders -- done")
    assert query.sql == f"SELECT order_id FROM orders LIMIT {MAX_ROWS}"


# --- Check 1: one statement, parseable -----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE orders",
        "SELECT * FROM orders; SELECT * FROM customers",
        "SELECT 1; SELECT pg_sleep(10)",
        # Postgres reads '\' as a complete string, so this is really 3 statements. sqlglot
        # reads it the same way, so the hidden DROP is caught.
        r"SELECT '\'; DROP TABLE orders; --'",
    ],
)
def test_multiple_statements_rejected(sql: str) -> None:
    assert_rejected(sql, "Only one statement")


@pytest.mark.parametrize("sql", ["", "   ", "\n\t", "-- just a comment", ";"])
def test_empty_rejected(sql: str) -> None:
    assert_rejected(sql, "empty")


@pytest.mark.parametrize(
    "sql",
    ["SELECT * FROM orders WHERE name = 'unterminated", "SELECT (1", "SELECT * FROM (orders"],
)
def test_unparseable_rejected(sql: str) -> None:
    # Fail closed: if we can't parse it, we can't prove it's safe.
    assert_rejected(sql, "could not be parsed")


def test_too_long_rejected() -> None:
    assert_rejected("SELECT 1 " + " " * MAX_SQL_CHARS, "too long")


# --- Check 2: only SELECT ------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO orders (order_id) VALUES (1)",
        "UPDATE products SET unit_price = 0",
        "DELETE FROM orders",
        "DROP TABLE customers",
        "ALTER TABLE products ADD COLUMN hacked int",
        "TRUNCATE order_details",
        "CREATE TABLE stolen AS SELECT * FROM customers",
        "GRANT SELECT ON orders TO public",
        "COPY customers TO '/tmp/customers.csv'",
        "SET statement_timeout = 0",
        "EXPLAIN ANALYZE SELECT * FROM orders",
        "VALUES (1), (2)",
        "TABLE orders",
        "BEGIN",
        "SELEC * FRM orders",  # sqlglot reads typos leniently, but it's still not a SELECT
        "dElEtE FrOm orders",  # mixed case changes nothing
        "/* harmless */ DELETE FROM orders",  # comment before the statement
    ],
)
def test_non_select_statements_rejected(sql: str) -> None:
    with pytest.raises(UnsafeSqlError):
        validate(sql)


# --- Check 3: no writes or locks hidden inside a SELECT --------------------------------------


@pytest.mark.parametrize(
    ("sql", "found"),
    [
        # Postgres runs data-modifying CTEs even though the outer statement is a SELECT.
        ("WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone", "DELETE"),
        ("WITH x AS (UPDATE products SET unit_price = 0 RETURNING *) SELECT * FROM x", "UPDATE"),
        (
            "WITH x AS (INSERT INTO orders (order_id) VALUES (1) RETURNING *) SELECT * FROM x",
            "INSERT",
        ),
        ("SELECT * INTO stolen FROM customers", "INTO"),  # creates a new table
        ("SELECT * FROM orders FOR UPDATE", "FOR UPDATE"),  # locks rows
        ("SELECT * FROM orders FOR SHARE", "FOR UPDATE / FOR SHARE"),
    ],
)
def test_hidden_writes_and_locks_rejected(sql: str, found: str) -> None:
    assert_rejected(sql, f"only read data \\({found}")


# --- Check 4: only allowed tables, only the public schema ------------------------------------


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("SELECT * FROM pg_catalog.pg_authid", "Table pg_catalog.pg_authid"),  # password hashes
        ("SELECT * FROM pg_class", "Table pg_class"),  # catalogs are found without a schema too
        ("SELECT * FROM pg_stat_activity", "Table pg_stat_activity"),  # other users' queries
        ("SELECT * FROM information_schema.tables", "Table information_schema.tables"),
        ("SELECT * FROM secret.salaries", "Table secret.salaries"),
        ("SELECT * FROM northwind.public.orders", "Table northwind.public.orders"),
        ("SELECT * FROM suppliers", "Table suppliers"),  # real, but not allowed for this user
        ('SELECT * FROM "Orders"', "Table Orders"),  # quoted = exact name, a different table
        ("SELECT * FROM orders o JOIN pg_roles r ON true", "Table pg_roles"),
        ("SELECT * FROM orders WHERE 1 = (SELECT 1 FROM pg_shadow)", "Table pg_shadow"),
    ],
)
def test_disallowed_tables_rejected(sql: str, reason: str) -> None:
    assert_rejected(sql, reason)


def test_cte_cannot_shadow_a_catalog_inside_its_own_body() -> None:
    # Not RECURSIVE, so inside the CTE body "pg_class" is the real system catalog.
    assert_rejected("WITH pg_class AS (SELECT * FROM pg_class) SELECT * FROM pg_class", "pg_class")


def test_cte_name_does_not_leak_out_of_its_subquery() -> None:
    # The CTE only exists inside the subquery; the outer pg_roles is the real catalog.
    assert_rejected(
        "SELECT * FROM (WITH pg_roles AS (SELECT 1 AS x) SELECT * FROM pg_roles) s, pg_roles",
        "pg_roles",
    )


def test_cte_with_a_catalog_name_is_fine_where_it_is_visible() -> None:
    query = validate("WITH pg_roles AS (SELECT * FROM orders) SELECT * FROM pg_roles")
    assert query.tables == frozenset({"orders"})


# --- Check 5: only allowlisted functions ---------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "function"),
    [
        ("SELECT pg_sleep(10)", "pg_sleep"),  # hangs the connection
        ("SELECT * FROM orders WHERE order_id IN (SELECT pg_sleep(10))", "pg_sleep"),
        ("SELECT pg_catalog.pg_sleep(10)", "pg_catalog.pg_sleep"),
        ('SELECT "pg_sleep"(10)', "pg_sleep"),
        ("SELECT PG_SLEEP(10)", "PG_SLEEP"),
        ("SELECT set_config('statement_timeout', '0', false)", "set_config"),  # kills timeout
        ("SELECT current_setting('data_directory')", "current_setting"),
        ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),  # reads server files
        ("SELECT lo_import('/etc/passwd')", "lo_import"),
        ("SELECT * FROM dblink('host=evil', 'SELECT 1') AS t(x int)", "dblink"),
        ("SELECT pg_terminate_backend(123)", "pg_terminate_backend"),  # kills sessions
        ("SELECT version()", "current_version"),  # sqlglot knows this one: typed, still blocked
        ("SELECT current_user", "current_user"),
        ("SELECT txid_current()", "txid_current"),
    ],
)
def test_disallowed_functions_rejected(sql: str, function: str) -> None:
    assert_rejected(sql, f"Function {function} is not allowed")


@pytest.mark.parametrize(
    ("sql", "function"),
    [
        # Same name as an allowed function, but a different function in another schema.
        ("SELECT other.age(hire_date) FROM employees", "other.age"),
        ("SELECT public.age(hire_date) FROM employees", "public.age"),
        ('SELECT "etl"."age"(hire_date) FROM employees', '"etl".age'),
        ("SELECT pg_catalog.upper(country) FROM customers", "pg_catalog.upper"),
    ],
)
def test_schema_qualified_functions_rejected(sql: str, function: str) -> None:
    with pytest.raises(UnsafeSqlError, match="call functions without a schema") as info:
        validate(sql)
    assert info.value.reason.startswith(f"Function {function} ")


# --- Check 6: row limit --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "expected_end"),
    [
        ("SELECT * FROM orders", f"LIMIT {MAX_ROWS}"),  # added
        ("SELECT * FROM orders LIMIT 5", "LIMIT 5"),  # smaller: kept
        ("SELECT * FROM orders LIMIT 999999", f"LIMIT {MAX_ROWS}"),  # bigger: lowered
        ("SELECT * FROM orders LIMIT ALL", f"LIMIT {MAX_ROWS}"),
        ("SELECT * FROM orders LIMIT NULL", f"LIMIT {MAX_ROWS}"),
        ("SELECT * FROM orders LIMIT (SELECT 5000)", f"LIMIT {MAX_ROWS}"),
        ("SELECT * FROM orders LIMIT 5 OFFSET 10", "LIMIT 5 OFFSET 10"),
        ("SELECT * FROM orders FETCH FIRST 3 ROWS ONLY", "FETCH FIRST 3 ROWS ONLY"),
        ("SELECT * FROM orders FETCH FIRST 5000 ROWS ONLY", f"LIMIT {MAX_ROWS}"),
        # A LIMIT inside a subquery doesn't limit the result: the outer query still gets one.
        ("SELECT * FROM (SELECT * FROM orders LIMIT 5) t", f"t LIMIT {MAX_ROWS}"),
        # On a UNION the LIMIT applies to the whole result.
        ("SELECT order_id FROM orders UNION SELECT 1", f"SELECT 1 LIMIT {MAX_ROWS}"),
        ("SELECT order_id FROM orders UNION SELECT 1 LIMIT 7", "SELECT 1 LIMIT 7"),
    ],
)
def test_row_limit_enforced(sql: str, expected_end: str) -> None:
    assert validate(sql).sql.endswith(expected_end)


def test_limit_equal_to_max_is_kept() -> None:
    assert validate(f"SELECT * FROM orders LIMIT {MAX_ROWS}").sql.endswith(f"LIMIT {MAX_ROWS}")


# --- ValidatedQuery and reporting ----------------------------------------------------------


def test_validated_query_cannot_be_forged() -> None:
    with pytest.raises(TypeError, match="only be created by validate_sql"):
        ValidatedQuery(sql="DROP TABLE orders", tables=frozenset(), _key=object())


def test_reason_is_available_for_the_user() -> None:
    with pytest.raises(UnsafeSqlError) as info:
        validate("DELETE FROM orders")
    assert info.value.reason == "Only SELECT queries are allowed."


def test_rejections_are_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(UnsafeSqlError):
        validate("SELECT pg_sleep(1)")
    [record] = [r for r in caplog.records if r.message == "unsafe sql rejected"]
    assert record.reason == "Function pg_sleep is not allowed."  # type: ignore[attr-defined]
    assert record.sql == "SELECT pg_sleep(1)"  # type: ignore[attr-defined]
