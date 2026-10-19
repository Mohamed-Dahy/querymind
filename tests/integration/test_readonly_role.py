"""Prove the agent's role (analyst_ro) can read business data but can never change anything.

Two independent layers protect the data:
  1. Every analyst_ro session starts read-only (default_transaction_read_only = on).
  2. Even if a session switches itself to read-write, the role has no write privileges.
"""

import psycopg
import pytest
from psycopg import errors, sql

from querymind.config import Settings

pytestmark = pytest.mark.integration

EXPECTED_ROWS = {
    "orders": 830,
    "order_details": 2155,
    "customers": 91,
    "products": 77,
    "employees": 9,
}

# Everything the agent must never be able to do.
WRITE_STATEMENTS = [
    "INSERT INTO shippers (shipper_id, company_name) VALUES (99, 'Hacker Freight')",
    "UPDATE products SET unit_price = 0",
    "DELETE FROM orders",
    "TRUNCATE order_details",
    "DROP TABLE customers",
    "ALTER TABLE products ADD COLUMN hacked integer",
    "CREATE TABLE stolen (id integer)",
    "CREATE ROLE hacker",
]


@pytest.mark.parametrize(("table", "expected"), EXPECTED_ROWS.items())
def test_can_select_business_tables(ro_conn: psycopg.Connection, table: str, expected: int) -> None:
    query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
    assert ro_conn.execute(query).fetchone() == (expected,)


def test_can_join_tables(ro_conn: psycopg.Connection) -> None:
    row = ro_conn.execute(
        "SELECT count(*) FROM orders o JOIN customers c ON c.customer_id = o.customer_id"
    ).fetchone()
    assert row == (830,)


# --- Layer 1: sessions start read-only ---------------------------------------------------


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_default_transaction_is_read_only(ro_conn: psycopg.Connection, statement: str) -> None:
    with pytest.raises(errors.ReadOnlySqlTransaction):
        ro_conn.execute(statement)


# --- Layer 2: privileges block writes even in a read-write transaction --------------------


@pytest.mark.parametrize("statement", WRITE_STATEMENTS)
def test_writes_blocked_by_privileges(ro_conn: psycopg.Connection, statement: str) -> None:
    # A misbehaving client could switch its own transaction to read-write...
    ro_conn.execute("SET TRANSACTION READ WRITE")
    # ...but the role still has no privilege to write.
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(statement)


def test_cannot_create_temp_tables(ro_conn: psycopg.Connection) -> None:
    ro_conn.execute("SET TRANSACTION READ WRITE")
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute("CREATE TEMP TABLE scratch (id integer)")


def test_cannot_read_password_hashes(ro_conn: psycopg.Connection) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute("SELECT rolpassword FROM pg_authid")


# --- Role settings ------------------------------------------------------------------------


def test_session_defaults(ro_conn: psycopg.Connection, settings: Settings) -> None:
    timeout = f"{settings.query_timeout_seconds}s"
    assert ro_conn.execute("SHOW statement_timeout").fetchone() == (timeout,)
    assert ro_conn.execute("SHOW default_transaction_read_only").fetchone() == ("on",)


def test_role_has_no_special_powers(ro_conn: psycopg.Connection) -> None:
    row = ro_conn.execute(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    assert row == (False, False, False, False, False)
