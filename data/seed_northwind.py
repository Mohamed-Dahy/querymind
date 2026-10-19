"""Load the Northwind sample database and create the agent's read-only role.

Run (after `docker compose up -d --wait`):
    uv run --env-file .env python data/seed_northwind.py

Safe to re-run: the Northwind tables are dropped and recreated, and the read-only
role's password and privileges are reset every time.
"""

import hashlib
import logging
import os
import sys
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql

logger = logging.getLogger("seed_northwind")

NORTHWIND_URL = "https://raw.githubusercontent.com/pthom/northwind_psql/master/northwind.sql"
# Pinned hash: if the upstream file ever changes, we refuse to run unknown SQL as superuser.
NORTHWIND_SHA256 = "0ee30c01ba282f7194f38bf7f99cd6be0470b7ee5f67d0f7ca41fb058d735e0c"
RAW_DIR = Path(__file__).parent / "raw"

# Tables the read-only role may SELECT. Listed explicitly (not "ALL TABLES") so tables we add
# later, like the audit log in Phase 5, are NOT readable by the agent unless we grant them.
BUSINESS_TABLES = (
    "categories",
    "customer_customer_demo",
    "customer_demographics",
    "customers",
    "employee_territories",
    "employees",
    "order_details",
    "orders",
    "products",
    "region",
    "shippers",
    "suppliers",
    "territories",
    "us_states",
)

# Any read-only query running longer than this is cancelled, so a runaway query can't hang the DB.
READ_ONLY_STATEMENT_TIMEOUT = "10s"

REQUIRED_ENV = (
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "POSTGRES_ADMIN_USER",
    "POSTGRES_ADMIN_PASSWORD",
    "ANALYST_RO_USER",
    "ANALYST_RO_PASSWORD",
)


class SeedError(Exception):
    """Seeding cannot continue (missing config or an unexpected download)."""


def read_env() -> dict[str, str]:
    """Return the required environment variables, or fail with a clear message."""
    missing = [name for name in REQUIRED_ENV if not os.environ.get(name)]
    if missing:
        raise SeedError(
            f"Missing environment variables: {', '.join(missing)}. "
            "Copy .env.example to .env, then run: "
            "uv run --env-file .env python data/seed_northwind.py"
        )
    return {name: os.environ[name] for name in REQUIRED_ENV}


def fetch_northwind_sql() -> str:
    """Download northwind.sql once (cached in data/raw/) and verify its SHA-256 hash."""
    path = RAW_DIR / "northwind.sql"
    if not path.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading %s", NORTHWIND_URL)
        with urllib.request.urlopen(NORTHWIND_URL, timeout=30) as response:
            path.write_bytes(response.read())

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != NORTHWIND_SHA256:
        # Delete it so a corrupted or partial download is fetched again next run.
        path.unlink()
        raise SeedError(
            f"northwind.sql hash mismatch (got {digest}). "
            "The upstream file changed or the download was corrupted. Not running it."
        )
    return data.decode("utf-8")


def load_northwind(conn: psycopg.Connection, script: str) -> None:
    """Enable pgvector and run the Northwind script (drops and recreates its tables)."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # One execute() can run many statements only when no parameters are passed.
    conn.execute(script)


def create_read_only_role(
    conn: psycopg.Connection, *, db_name: str, user: str, password: str
) -> None:
    """Create (or reset) the read-only role and grant SELECT on business tables only."""
    role = sql.Identifier(user)
    database = sql.Identifier(db_name)
    # A login role with no special powers. sql.Literal quotes the password safely.
    attributes = sql.SQL(
        "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Literal(password))

    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    if exists:
        conn.execute(sql.SQL("ALTER ROLE {} WITH {}").format(role, attributes))
    else:
        conn.execute(sql.SQL("CREATE ROLE {} WITH {}").format(role, attributes))

    statements: list[sql.SQL | sql.Composed] = [
        # Session defaults for every connection this role opens.
        sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
            role, sql.Literal(READ_ONLY_STATEMENT_TIMEOUT)
        ),
        sql.SQL("ALTER ROLE {} SET default_transaction_read_only = on").format(role),
        # Postgres gives every role (PUBLIC) CONNECT + TEMP on new databases; take that away,
        # then let only our role connect. TEMP stays revoked: no temp tables for the agent.
        sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database),
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role),
        # Same for the schema: nobody but the owner can create objects in it.
        sql.SQL("REVOKE ALL ON SCHEMA public FROM PUBLIC"),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role),
        # Start clean, then grant exactly SELECT on the business tables.
        sql.SQL("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}").format(role),
        sql.SQL("GRANT SELECT ON {} TO {}").format(
            sql.SQL(", ").join(sql.Identifier(table) for table in BUSINESS_TABLES), role
        ),
    ]
    for statement in statements:
        conn.execute(statement)


def count_rows(conn: psycopg.Connection) -> dict[str, int]:
    """Return the row count of every business table (a quick sanity check)."""
    counts: dict[str, int] = {}
    for table in BUSINESS_TABLES:
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
        counts[table] = row[0] if row else 0
    return counts


def main() -> int:
    """Seed the database. Returns a process exit code (0 = success)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        env = read_env()
        script = fetch_northwind_sql()
        # `with` = one transaction: commit if everything succeeds, roll back everything if not.
        with psycopg.connect(
            host=env["POSTGRES_HOST"],
            port=int(env["POSTGRES_PORT"]),
            dbname=env["POSTGRES_DB"],
            user=env["POSTGRES_ADMIN_USER"],
            password=env["POSTGRES_ADMIN_PASSWORD"],
            connect_timeout=10,
        ) as conn:
            load_northwind(conn, script)
            create_read_only_role(
                conn,
                db_name=env["POSTGRES_DB"],
                user=env["ANALYST_RO_USER"],
                password=env["ANALYST_RO_PASSWORD"],
            )
            counts = count_rows(conn)
    except (SeedError, psycopg.Error, OSError) as exc:
        logger.error("Seeding failed: %s", exc)
        return 1

    logger.info(
        "Loaded %d tables: %s", len(counts), ", ".join(f"{t}={n}" for t, n in counts.items())
    )
    logger.info("Read-only role %r is ready.", env["ANALYST_RO_USER"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
