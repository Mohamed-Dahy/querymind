"""data/seed_bird.py: each BIRD database is readable by the agent's role, never writable, and its
unusual names (capitals, spaces, reserved words) reach the LLM written so that SQL works.

Needs `uv run python data/seed_bird.py` (~800 MB download, once). Skipped when BIRD isn't loaded,
so CI and fresh clones are unaffected.
"""

from collections.abc import Iterator

import psycopg
import pytest

from evals.bird import database_name
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.executor import execute_query
from querymind.db.schema import describe_schema, introspect_schema
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

pytestmark = pytest.mark.integration

# Tables per database, from BIRD's dev_tables.json.
TABLE_COUNTS = {
    "california_schools": 3,
    "card_games": 6,
    "codebase_community": 8,
    "debit_card_specializing": 5,
    "european_football_2": 7,
    "financial": 8,
    "formula_1": 13,
    "student_club": 8,
    "superhero": 10,
    "thrombosis_prediction": 3,
    "toxicology": 4,
}


def bird_conn(settings: Settings, db_id: str) -> psycopg.Connection:
    """A read-only connection to one BIRD database, or skip the test if BIRD isn't loaded."""
    try:
        return connect_readonly(settings.model_copy(update={"postgres_db": database_name(db_id)}))
    except DatabaseUnavailableError as exc:
        pytest.skip(f"BIRD not loaded ({exc.__cause__}): uv run python data/seed_bird.py")


@pytest.fixture
def financial(settings: Settings) -> Iterator[psycopg.Connection]:
    conn = bird_conn(settings, "financial")
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize(("db_id", "tables"), TABLE_COUNTS.items())
def test_each_bird_database_has_its_own_tables(settings: Settings, db_id: str, tables: int) -> None:
    with bird_conn(settings, db_id) as conn:
        schema = introspect_schema(conn, sample_rows=0)
    # Only this database's tables: no Northwind, no other BIRD database.
    assert len(schema.tables) == tables
    assert "customers" not in schema.table_names or db_id == "debit_card_specializing"


@pytest.mark.parametrize(
    "statement",
    [
        'DELETE FROM "order"',
        "UPDATE account SET frequency = 'x'",
        "DROP TABLE loan",
        "CREATE TABLE stolen (id int)",
    ],
)
def test_the_read_only_role_cannot_change_bird(
    financial: psycopg.Connection, statement: str
) -> None:
    with pytest.raises(psycopg.errors.Error):
        financial.execute(statement)
    financial.rollback()


def test_reserved_word_tables_reach_the_llm_quoted(financial: psycopg.Connection) -> None:
    text = describe_schema(introspect_schema(financial))
    assert 'Table "order"' in text
    assert "Table order\n" not in text  # bare, FROM order would be a syntax error


def test_spaced_and_mixed_case_columns_reach_the_llm_quoted(settings: Settings) -> None:
    with bird_conn(settings, "california_schools") as conn:
        text = describe_schema(introspect_schema(conn))
    assert '"Free Meal Count (K-12)" real' in text
    with bird_conn(settings, "thrombosis_prediction") as conn:
        text = describe_schema(introspect_schema(conn))
    assert '"Examination Date" date' in text


def test_quoted_names_validate_and_run(financial: psycopg.Connection) -> None:
    # Written the way the schema text shows it: works, through validator and executor.
    tables = introspect_schema(financial, sample_rows=0).table_names
    query = validate_sql('SELECT count(*) FROM "order"', allowed_tables=tables, max_rows=10)
    [[orders]] = execute_query(financial, query, timeout_seconds=10).rows
    assert isinstance(orders, int) and orders > 0
    # "Order" is a different name in Postgres (quoted = exact case): not a table here.
    with pytest.raises(UnsafeSqlError, match="Order is not allowed"):
        validate_sql('SELECT count(*) FROM "Order"', allowed_tables=tables, max_rows=10)
