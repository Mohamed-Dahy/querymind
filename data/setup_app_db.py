"""Create the app's own storage: a schema for saved conversations, and a role that can only use it.

Run (after `docker compose up -d --wait` and data/seed_northwind.py, with APP_DB_USER and
APP_DB_PASSWORD in .env):
    uv run python data/setup_app_db.py

Safe to re-run: nothing is dropped, and the role's password and privileges are reset every time.

The agent and the app never share a role. `analyst_ro` reads business tables and can't see this
schema; the app's role reads and writes this schema and can't see business tables. The admin owns
the tables, so the app's role can change rows but can't drop or alter anything.
"""

import logging
import sys

import psycopg
from psycopg import sql

from querymind.config import ConfigError, get_settings

logger = logging.getLogger("setup_app_db")

SCHEMA = "querymind_app"
APP_STATEMENT_TIMEOUT = "5s"  # these are small queries: anything slower is a bug

# IF NOT EXISTS: running this again leaves saved conversations as they are.
TABLES = (
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.conversations (
        id uuid PRIMARY KEY,
        title text NOT NULL CHECK (char_length(title) BETWEEN 1 AND 200),
        pinned boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.turns (
        conversation_id uuid NOT NULL REFERENCES {SCHEMA}.conversations (id) ON DELETE CASCADE,
        n integer NOT NULL CHECK (n >= 1),
        question text NOT NULL,
        kind text NOT NULL CHECK (kind IN ('answer', 'clarification', 'refusal')),
        sql text,
        -- The API's reply. Rows may hold personal data, so the database itself refuses them.
        reply jsonb NOT NULL CHECK (NOT reply ? 'rows'),
        trace jsonb NOT NULL DEFAULT '[]',
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (conversation_id, n)
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.saved_questions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        question text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    # One row per wording. On a hash, because a plain index refuses a value over 2,704 bytes, and
    # 1,000 characters (the longest question) can be 4,000. The DROP is for a database set up before
    # this index existed.
    f"ALTER TABLE {SCHEMA}.saved_questions DROP CONSTRAINT IF EXISTS saved_questions_question_key",
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS saved_questions_wording
    ON {SCHEMA}.saved_questions (md5(question))
    """,
    # What the history list sorts by.
    f"""
    CREATE INDEX IF NOT EXISTS conversations_list
        ON {SCHEMA}.conversations (pinned DESC, updated_at DESC)
    """,
)


def set_up(conn: psycopg.Connection, *, db_name: str, user: str, password: str) -> None:
    """Create (or reset) the role, the schema and its tables, in the caller's transaction."""
    role = sql.Identifier(user)
    schema = sql.Identifier(SCHEMA)
    database = sql.Identifier(db_name)
    # A login role with no special powers. sql.Literal quotes the password safely.
    attributes = sql.SQL(
        "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}"
    ).format(sql.Literal(password))
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    verb = sql.SQL("ALTER" if exists else "CREATE")
    conn.execute(sql.SQL("{} ROLE {} WITH {}").format(verb, role, attributes))

    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(schema))
    for table in TABLES:
        conn.execute(table.encode())

    tables = sql.SQL("ALL TABLES IN SCHEMA {}").format(schema)
    statements: list[sql.Composed] = [
        sql.SQL("ALTER ROLE {} SET statement_timeout = {}").format(
            role, sql.Literal(APP_STATEMENT_TIMEOUT)
        ),
        # An unqualified name can only ever mean this schema.
        sql.SQL("ALTER ROLE {} SET search_path = {}").format(role, schema),
        # seed_northwind.py already took CONNECT away from PUBLIC, so each role is granted its own.
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role),
        sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(schema),
        sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role),
        # Start clean, then grant exactly this: rows only, so no DROP, ALTER or TRUNCATE.
        sql.SQL("REVOKE ALL ON {} FROM {}").format(tables, role),
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(tables, role),
    ]
    for statement in statements:
        conn.execute(statement)


def main() -> int:
    """Set up the app's storage. Returns a process exit code (0 = success)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        settings = get_settings()
        if settings.app_db_user is None or settings.app_db_password is None:
            raise ConfigError("Set APP_DB_USER and APP_DB_PASSWORD in .env first (.env.example)")
        # Like seeding, this is a job for the admin role. `with` = one transaction.
        with psycopg.connect(settings.admin_conninfo()) as conn:
            set_up(
                conn,
                db_name=settings.postgres_db,
                user=settings.app_db_user,
                password=settings.app_db_password.get_secret_value(),
            )
    except (ConfigError, psycopg.Error) as exc:
        logger.error("Setting up the app's storage failed: %s", exc)
        return 1
    logger.info("App storage is ready: schema %r, role %r.", SCHEMA, settings.app_db_user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
