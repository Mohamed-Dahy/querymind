"""Load the Northwind sample database and create the agent's read-only role.

Run (after `docker compose up -d --wait`):
    uv run python data/seed_northwind.py
    uv run python data/seed_northwind.py --row-security-only   # just (re)create the RLS policies

Safe to re-run: the Northwind tables are dropped and recreated, and the read-only
role's password and privileges are reset every time. The reset revokes every table grant,
so after re-running this, run data/seed_chinook.py again too (Phase 3.2).

Row-level security (5.3): `orders` and `order_details` get a policy that lets a session see only
the orders of the sales regions in its `querymind.scope` setting (`*` = every row; not set = no
row). See src/querymind/safety/row_scope.py. `--row-security-only` adds them to a database that
is already seeded, without reloading anything.
"""

import hashlib
import logging
import sys
import urllib.request
from pathlib import Path

import psycopg
from psycopg import sql

from querymind.config import ConfigError, get_settings

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


class SeedError(Exception):
    """Seeding cannot continue (an unexpected download)."""


def fetch_pinned(url: str, filename: str, sha256: str) -> str:
    """Download a SQL file once (cached in data/raw/) and verify its SHA-256 hash.

    Also used by seed_chinook.py: every script we run as superuser must be pinned.
    """
    path = RAW_DIR / filename
    if not path.exists():
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading %s", url)
        with urllib.request.urlopen(url, timeout=30) as response:
            path.write_bytes(response.read())

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        # Delete it so a corrupted or partial download is fetched again next run.
        path.unlink()
        raise SeedError(
            f"{filename} hash mismatch (got {digest}). "
            "The upstream file changed or the download was corrupted. Not running it."
        )
    return data.decode("utf-8")


def fetch_northwind_sql() -> str:
    return fetch_pinned(NORTHWIND_URL, "northwind.sql", NORTHWIND_SHA256)


def load_northwind(conn: psycopg.Connection, script: str) -> None:
    """Enable pgvector and run the Northwind script (drops and recreates its tables)."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # One execute() can run many statements only when no parameters are passed.
    conn.execute(script)


def create_read_only_role(
    conn: psycopg.Connection, *, db_name: str, user: str, password: str, timeout_seconds: int
) -> None:
    """Create (or reset) the read-only role and grant SELECT on business tables only.

    `timeout_seconds` becomes the role's default statement_timeout: any query running longer
    is cancelled, so a runaway query can't hang the database.
    """
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
            role, sql.Literal(f"{timeout_seconds}s")
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


# 5.3: an order is in the sales region of the employee who took it (each Northwind employee works
# in exactly one region). current_setting(..., true): NULL instead of an error when it isn't set,
# and NULL matches nothing, so a session that never set it sees no order (fail closed).
#
# The region lookup is a SECURITY DEFINER function: it runs with its owner's rights (the admin
# who seeds), so a login that may read `orders` doesn't also need `employee_territories`,
# `territories` and `region`. A fixed `search_path` stops a caller from slipping in their own
# `region` table. Inside it the admin's rights bypass RLS, so `querymind_order_in_scope` checks the
# region itself instead of relying on the orders policy.
ROW_SECURITY = (
    """
    CREATE OR REPLACE FUNCTION querymind_employee_in_scope(employee smallint) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT coalesce(
            current_setting('querymind.scope', true) = '*'
            OR EXISTS (
                SELECT 1
                FROM employee_territories AS et
                JOIN territories AS t ON t.territory_id = et.territory_id
                JOIN region AS r ON r.region_id = t.region_id
                WHERE et.employee_id = employee
                    AND trim(r.region_description) = ANY (
                        string_to_array(current_setting('querymind.scope', true), ',')
                    )
            ),
            false
        )
    $$
    """,
    """
    CREATE OR REPLACE FUNCTION querymind_order_in_scope(order_number smallint) RETURNS boolean
    LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
        SELECT coalesce(
            (SELECT querymind_employee_in_scope(o.employee_id) FROM orders AS o
             WHERE o.order_id = order_number),
            false
        )
    $$
    """,
    "ALTER TABLE orders ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS querymind_scope ON orders",
    """
    CREATE POLICY querymind_scope ON orders FOR SELECT
        USING (querymind_employee_in_scope(employee_id))
    """,
    # A line of an order is visible when its order is, so sums of order lines can't reach other
    # regions either.
    "ALTER TABLE order_details ENABLE ROW LEVEL SECURITY",
    "DROP POLICY IF EXISTS querymind_scope ON order_details",
    """
    CREATE POLICY querymind_scope ON order_details FOR SELECT
        USING (querymind_order_in_scope(order_id))
    """,
)


def enable_row_security(conn: psycopg.Connection) -> None:
    """(Re)create the row-level security policies (5.3). Safe to run again."""
    for statement in ROW_SECURITY:
        conn.execute(statement)  # fixed text, no user input


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
    if "--row-security-only" in sys.argv[1:]:
        return row_security_only()
    try:
        settings = get_settings()
        script = fetch_northwind_sql()
        # Seeding is the one job that uses the admin role.
        # `with` = one transaction: commit if everything succeeds, roll back everything if not.
        with psycopg.connect(settings.admin_conninfo()) as conn:
            load_northwind(conn, script)
            create_read_only_role(
                conn,
                db_name=settings.postgres_db,
                user=settings.analyst_ro_user,
                password=settings.analyst_ro_password.get_secret_value(),
                timeout_seconds=settings.query_timeout_seconds,
            )
            enable_row_security(conn)
            counts = count_rows(conn)
    except (ConfigError, SeedError, psycopg.Error, OSError) as exc:
        logger.error("Seeding failed: %s", exc)
        return 1

    logger.info(
        "Loaded %d tables: %s", len(counts), ", ".join(f"{t}={n}" for t, n in counts.items())
    )
    logger.info("Read-only role %r is ready.", settings.analyst_ro_user)
    logger.info("Row-level security is on for orders and order_details.")
    return 0


def row_security_only() -> int:
    """Add the 5.3 policies to an already seeded database (no reload, no grant reset)."""
    try:
        with psycopg.connect(get_settings().admin_conninfo()) as conn:
            enable_row_security(conn)
    except (ConfigError, psycopg.Error) as exc:
        logger.error("Row-level security failed: %s", exc)
        return 1
    logger.info("Row-level security is on for orders and order_details.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
