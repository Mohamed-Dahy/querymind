"""Load the Chinook music-store sample next to Northwind, and let the read-only role read it.

Run (after data/seed_northwind.py, which creates the read-only role):
    uv run python data/seed_chinook.py              # load + grant SELECT to the agent's role
    uv run python data/seed_chinook.py --no-grant   # load only: the agent doesn't see Chinook

Phase 3 puts both samples in one database (25 tables) to test schema retrieval on a bigger
schema. Chinook's snake_case names don't clash with Northwind's (customer vs customers).

Safe to re-run: the 11 Chinook tables are dropped and recreated. The upstream script starts
by dropping and creating a `chinook` *database* and switching to it (`\\c`, a psql command);
we skip that part and run it from the first CREATE TABLE, into this database.
"""

import argparse
import logging
import sys

import psycopg
from psycopg import sql
from seed_northwind import SeedError, fetch_pinned

from querymind.config import ConfigError, get_settings

logger = logging.getLogger("seed_chinook")

CHINOOK_URL = (
    "https://github.com/lerocha/chinook-database/releases/download/v1.4.5/Chinook_PostgreSql.sql"
)
# Pinned hash: if the release file ever changes, we refuse to run unknown SQL as superuser.
CHINOOK_SHA256 = "e3fde5c1a5b51a2a91429a702c9ca6e69ba56e6c7f5e112724d70c3d03db695e"

# Listed explicitly, like Northwind's BUSINESS_TABLES: only these become readable.
CHINOOK_TABLES = (
    "album",
    "artist",
    "customer",
    "employee",
    "genre",
    "invoice",
    "invoice_line",
    "media_type",
    "playlist",
    "playlist_track",
    "track",
)


def tables_part(script: str) -> str:
    """The script from its first CREATE TABLE on: no DROP/CREATE DATABASE, no `\\c`."""
    start = script.find("CREATE TABLE")
    if start == -1:
        raise SeedError("Chinook script has no CREATE TABLE: not the expected file.")
    return script[start:]


def load_chinook(conn: psycopg.Connection, script: str) -> None:
    """Drop the Chinook tables if they exist, then create and fill them."""
    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
            sql.SQL(", ").join(sql.Identifier(table) for table in CHINOOK_TABLES)
        )
    )
    conn.execute(tables_part(script))  # many statements in one call: no parameters


def grant_read(conn: psycopg.Connection, user: str) -> None:
    """SELECT on the Chinook tables for the read-only role (created by seed_northwind.py)."""
    if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone():
        raise SeedError(f"Role {user!r} doesn't exist: run data/seed_northwind.py first.")
    conn.execute(
        sql.SQL("GRANT SELECT ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier(table) for table in CHINOOK_TABLES),
            sql.Identifier(user),
        )
    )


def count_rows(conn: psycopg.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in CHINOOK_TABLES:
        row = conn.execute(
            sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        ).fetchone()
        counts[table] = row[0] if row else 0
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Load Chinook next to Northwind.")
    parser.add_argument(
        "--no-grant", action="store_true", help="load only; the agent's role can't read it"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = get_settings()
        script = fetch_pinned(CHINOOK_URL, "chinook.sql", CHINOOK_SHA256)
        # One transaction as the admin role: all of it, or nothing.
        with psycopg.connect(settings.admin_conninfo()) as conn:
            load_chinook(conn, script)
            if not args.no_grant:
                grant_read(conn, settings.analyst_ro_user)
            counts = count_rows(conn)
    except (ConfigError, SeedError, psycopg.Error, OSError) as exc:
        logger.error("Seeding Chinook failed: %s", exc)
        return 1

    logger.info(
        "Loaded %d tables: %s", len(counts), ", ".join(f"{t}={n}" for t, n in counts.items())
    )
    if args.no_grant:
        logger.info("Not granted: %r can't read Chinook.", settings.analyst_ro_user)
    else:
        logger.info("Read-only role %r can now read Chinook.", settings.analyst_ro_user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
