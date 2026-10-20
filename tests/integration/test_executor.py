"""execute_query against the real Northwind database, as analyst_ro."""

import psycopg
import pytest

from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.db.executor import execute_query
from querymind.db.schema import introspect_schema
from querymind.safety import sql_validator
from querymind.safety.sql_validator import ValidatedQuery, validate_sql

pytestmark = pytest.mark.integration

NORTHWIND_TABLES = frozenset({"orders", "customers", "products", "order_details", "categories"})


def validated(sql: str, *, max_rows: int = 100) -> ValidatedQuery:
    return validate_sql(sql, allowed_tables=NORTHWIND_TABLES, max_rows=max_rows)


def forged(sql: str) -> ValidatedQuery:
    """Simulate a validator bug: SQL that never went through validate_sql.

    Uses the private key on purpose, to prove the layers *after* the validator still hold.
    """
    return ValidatedQuery(
        sql=sql, tables=frozenset(), max_rows=100, _key=sql_validator._VALIDATOR_KEY
    )


def test_returns_columns_and_json_safe_rows(ro_conn: psycopg.Connection) -> None:
    result = execute_query(
        ro_conn,
        validated(
            "SELECT order_id, order_date, freight, 1.5::numeric AS ratio "
            "FROM orders ORDER BY order_id LIMIT 2"
        ),
        timeout_seconds=5,
    )
    assert result.columns == ["order_id", "order_date", "freight", "ratio"]
    assert result.rows[0][:2] == [10248, "1996-07-04"]
    assert result.rows[0][3] == 1.5  # Decimal became float
    assert result.row_count == 2
    assert result.truncated is False
    assert result.duration_ms >= 0


def test_aggregate_matches_known_northwind_value(ro_conn: psycopg.Connection) -> None:
    result = execute_query(ro_conn, validated("SELECT count(*) FROM orders"), timeout_seconds=5)
    assert result.rows == [[830]]


def test_more_rows_than_max_are_cut_and_flagged(ro_conn: psycopg.Connection) -> None:
    result = execute_query(
        ro_conn, validated("SELECT * FROM orders", max_rows=5), timeout_seconds=5
    )
    assert result.row_count == 5
    assert len(result.rows) == 5
    assert result.truncated is True


def test_exactly_max_rows_is_not_truncated(ro_conn: psycopg.Connection) -> None:
    # 8 categories in Northwind, max_rows 8: everything fits, nothing was cut.
    result = execute_query(
        ro_conn, validated("SELECT * FROM categories", max_rows=8), timeout_seconds=5
    )
    assert result.row_count == 8
    assert result.truncated is False


def test_empty_result(ro_conn: psycopg.Connection) -> None:
    result = execute_query(
        ro_conn, validated("SELECT * FROM orders WHERE order_id < 0"), timeout_seconds=5
    )
    assert result.rows == []
    assert result.truncated is False
    assert "order_id" in result.columns  # columns are known even with no rows


def test_slow_query_times_out(ro_conn: psycopg.Connection) -> None:
    # pg_sleep is blocked by the validator, so use an allowed but very slow query.
    slow = validated("SELECT count(*) FROM generate_series(1, 1000000000) AS g(n)")
    with pytest.raises(QueryTimeoutError, match="longer than 1 s"):
        execute_query(ro_conn, slow, timeout_seconds=1)


def test_sql_error_has_a_clean_message(ro_conn: psycopg.Connection) -> None:
    with pytest.raises(QueryExecutionError) as info:
        execute_query(ro_conn, validated("SELECT no_such_column FROM orders"), timeout_seconds=5)
    assert info.value.detail == 'column "no_such_column" does not exist'
    assert str(info.value) == (
        'The database could not run the query: column "no_such_column" does not exist'
    )


def test_connection_is_usable_after_an_error(ro_conn: psycopg.Connection) -> None:
    with pytest.raises(QueryExecutionError):
        execute_query(ro_conn, validated("SELECT 1 / 0"), timeout_seconds=5)
    result = execute_query(ro_conn, validated("SELECT 1"), timeout_seconds=5)
    assert result.rows == [[1]]


def test_works_after_other_queries_on_the_same_connection(ro_conn: psycopg.Connection) -> None:
    # The CLI reuses one connection: introspection first, then queries.
    introspect_schema(ro_conn)
    result = execute_query(ro_conn, validated("SELECT count(*) FROM products"), timeout_seconds=5)
    assert result.rows == [[77]]


def test_timeout_does_not_stick_to_the_connection(ro_conn: psycopg.Connection) -> None:
    execute_query(ro_conn, validated("SELECT 1"), timeout_seconds=1)
    # SET LOCAL only lasts until the end of that query's transaction.
    [(timeout,)] = ro_conn.execute("SHOW statement_timeout").fetchall()
    assert timeout != "1s"


# --- Defense in depth: even SQL that skipped the validator can't write ---------------------


@pytest.mark.parametrize(
    ("sql", "stopped_by"),
    [
        # Layer: the server-side cursor. DECLARE ... CURSOR only accepts a query.
        ("DELETE FROM orders", 'syntax error at or near "DELETE"'),
        ("UPDATE products SET unit_price = 0", 'syntax error at or near "UPDATE"'),
        (
            "WITH gone AS (DELETE FROM orders RETURNING *) SELECT count(*) FROM gone",
            "DECLARE CURSOR must not contain data-modifying statements in WITH",
        ),
        ("SELECT * INTO stolen FROM orders", "SELECT ... INTO is not allowed here"),
        # Layer: the READ ONLY transaction. A write disguised as a SELECT still fails.
        ("SELECT lo_create(0)", "cannot execute lo_create() in a read-only transaction"),
        # Layer: the role's privileges.
        ("SELECT * FROM pg_authid", "permission denied for table pg_authid"),
    ],
)
def test_writes_fail_even_without_the_validator(
    ro_conn: psycopg.Connection, sql: str, stopped_by: str
) -> None:
    with pytest.raises(QueryExecutionError) as info:
        execute_query(ro_conn, forged(sql), timeout_seconds=5)
    assert info.value.detail == stopped_by
    result = execute_query(ro_conn, validated("SELECT count(*) FROM orders"), timeout_seconds=5)
    assert result.rows == [[830]]  # nothing was deleted
