"""The explorer against the real database: every table, real counts, and no personal value."""

from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient

from querymind.api.explorer import create_explorer
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.db.schema import quote_name, read_row_counts
from querymind.semantic.loader import load_semantic_layers

pytestmark = pytest.mark.integration


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    app = create_app(pipeline=object(), explorer=create_explorer(settings))  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def exact_count(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {quote_name(table)}").fetchone()
    assert row is not None
    count: int = row[0]
    return count


def test_every_table_has_a_business_and_a_row_count(api: TestClient) -> None:
    tables = api.get("/catalog").json()["tables"]

    assert len(tables) >= 25
    assert [table["name"] for table in tables if table["business"] is None] == []
    assert [table["name"] for table in tables if table["row_count"] is None] == []


def test_a_table_postgres_has_no_estimate_for_still_gets_its_count(
    ro_conn: psycopg.Connection,
) -> None:
    """Whatever ANALYZE has seen by now: every count is right for tables this small."""
    tables = ["genre", "media_type", "playlist", "employee", "region", "shippers"]

    counts = read_row_counts(ro_conn, tables)

    assert counts == {table: exact_count(ro_conn, table) for table in tables}


def test_never_analyzed_means_counting(ro_conn: psycopg.Connection) -> None:
    """A table Postgres has never analyzed has the estimate -1. It must not show as -1."""
    counts = read_row_counts(ro_conn, ["customer_demographics", "customer_customer_demo"])

    assert all(count is not None and count >= 0 for count in counts.values())


def test_a_table_the_role_cant_count_is_unknown_and_the_others_still_count(
    ro_conn: psycopg.Connection,
) -> None:
    counts = read_row_counts(ro_conn, ["no_such_table", "genre"])

    assert counts["no_such_table"] is None
    assert counts["genre"] == exact_count(ro_conn, "genre")


def test_foreign_keys_are_there_for_the_read_only_role(api: TestClient) -> None:
    tables = api.get("/catalog").json()["tables"]

    assert sum(len(table["foreign_keys"]) for table in tables) >= 24


def test_no_value_of_any_pii_column_reaches_the_client(
    api: TestClient, settings: Settings, ro_conn: psycopg.Connection
) -> None:
    """The real leak test: every real value of every personal column, against both responses."""
    text = api.get("/catalog").text + api.get("/semantic").text

    found = []
    for layer in load_semantic_layers(settings.semantic_layer_paths):
        for table, column in sorted(layer.pii_columns):
            query = (
                f"SELECT DISTINCT {quote_name(column)}::text FROM {quote_name(table)} "
                f"WHERE {quote_name(column)} IS NOT NULL"
            )
            for (value,) in ro_conn.execute(query).fetchall():
                # Shorter values ("USA", a two-letter title) also occur as parts of ordinary words.
                if len(value) >= 4 and value in text:
                    found.append((table, column))
    assert found == []


def test_the_semantic_overview_covers_every_table_in_the_catalog(api: TestClient) -> None:
    catalog = {table["name"] for table in api.get("/catalog").json()["tables"]}
    described = {
        table["name"]
        for business in api.get("/semantic").json()["businesses"]
        for table in business["tables"]
    }

    assert described == catalog


def test_suggested_questions_cover_most_tables(api: TestClient) -> None:
    tables = [
        table
        for business in api.get("/semantic").json()["businesses"]
        for table in business["tables"]
    ]

    without = {table["name"] for table in tables if not table["suggested_questions"]}
    assert without <= {"customer_customer_demo", "customer_demographics", "us_states"}
