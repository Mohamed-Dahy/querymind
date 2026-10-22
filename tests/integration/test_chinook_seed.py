"""data/seed_chinook.py: Chinook sits next to Northwind; the agent's role can read it, never write.

Needs both seeds: `uv run python data/seed_northwind.py` then `uv run python data/seed_chinook.py`.
"""

import psycopg
import pytest

pytestmark = pytest.mark.integration

ROW_COUNTS = {
    "album": 347,
    "artist": 275,
    "customer": 59,
    "employee": 8,
    "genre": 25,
    "invoice": 412,
    "invoice_line": 2240,
    "media_type": 5,
    "playlist": 18,
    "playlist_track": 8715,
    "track": 3503,
}


@pytest.mark.parametrize(("table", "rows"), ROW_COUNTS.items())
def test_every_chinook_table_is_loaded_and_readable(
    ro_conn: psycopg.Connection, table: str, rows: int
) -> None:
    query = psycopg.sql.SQL("SELECT count(*) FROM {}").format(psycopg.sql.Identifier(table))
    assert ro_conn.execute(query).fetchone() == (rows,)


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM invoice_line",
        "UPDATE track SET unit_price = 0",
        "INSERT INTO genre (genre_id, name) VALUES (999, 'x')",
        "DROP TABLE playlist_track",
    ],
)
def test_the_read_only_role_cannot_change_chinook(
    ro_conn: psycopg.Connection, statement: str
) -> None:
    # Two locks: the role's sessions are read-only, and it has no write privilege anyway.
    with pytest.raises(psycopg.errors.Error):
        ro_conn.execute(statement)
    ro_conn.rollback()
