"""Create the app's own storage: a schema for saved conversations, and a role that can only use it.

Run (after `docker compose up -d --wait` and data/seed_northwind.py, with APP_DB_USER and
APP_DB_PASSWORD in .env):
    uv run python data/setup_app_db.py

Safe to re-run: no table or row is dropped, and the role's password and privileges are reset every
time. Since 5.1 it also holds the accounts (users, workspaces, memberships, refresh tokens), and
conversations belong to a user; since 5.7 it holds users' encrypted secrets too. Conversations saved
before 5.1 have no owner; give them to your account with
    uv run python data/setup_app_db.py --owner you@example.com

The agent and the app never share a role. `analyst_ro` reads business tables and can't see this
schema; the app's role reads and writes this schema and can't see business tables. The admin owns
the tables, so the app's role can change rows but can't drop or alter anything.
"""

import argparse
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
    # One row per wording (per user, below). On a hash, because a plain index refuses a value over
    # 2,704 bytes, and 1,000 characters (the longest question) can be 4,000. The DROP is for a
    # database set up before that index existed.
    f"ALTER TABLE {SCHEMA}.saved_questions DROP CONSTRAINT IF EXISTS saved_questions_question_key",
    # --- Accounts (5.1) ---
    # Emails are stored lowercased, so "Ana@X.com" and "ana@x.com" are one account.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.users (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email text NOT NULL UNIQUE CHECK (email = lower(email) AND char_length(email) <= 254),
        password_hash text NOT NULL,
        disabled boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.workspaces (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.memberships (
        workspace_id uuid NOT NULL REFERENCES {SCHEMA}.workspaces (id) ON DELETE CASCADE,
        user_id uuid NOT NULL REFERENCES {SCHEMA}.users (id) ON DELETE CASCADE,
        role text NOT NULL CHECK (role IN ('admin', 'analyst', 'viewer')),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (workspace_id, user_id)
    )
    """,
    # Only hashes of refresh tokens. One family per login (see src/querymind/store/accounts.py).
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.refresh_tokens (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id uuid NOT NULL REFERENCES {SCHEMA}.users (id) ON DELETE CASCADE,
        workspace_id uuid NOT NULL REFERENCES {SCHEMA}.workspaces (id) ON DELETE CASCADE,
        family_id uuid NOT NULL,
        token_hash text NOT NULL UNIQUE,
        expires_at timestamptz NOT NULL,
        used_at timestamptz,
        revoked_at timestamptz,
        created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS refresh_tokens_family ON {SCHEMA}.refresh_tokens (family_id)",
    # Every saved conversation and question belongs to a user, in a workspace. Rows saved before
    # 5.1 have none: nobody sees them until `--owner` gives them to someone.
    *(
        f"""
        ALTER TABLE {SCHEMA}.{table}
            ADD COLUMN IF NOT EXISTS user_id uuid REFERENCES {SCHEMA}.users (id) ON DELETE CASCADE,
            ADD COLUMN IF NOT EXISTS workspace_id uuid
                REFERENCES {SCHEMA}.workspaces (id) ON DELETE CASCADE
        """
        for table in ("conversations", "saved_questions")
    ),
    # The same words saved once per user, not once for everyone.
    f"DROP INDEX IF EXISTS {SCHEMA}.saved_questions_wording",
    f"""
    CREATE UNIQUE INDEX IF NOT EXISTS saved_questions_wording_per_user
    ON {SCHEMA}.saved_questions (user_id, md5(question))
    """,
    # --- The audit log (5.5): one row per question, re-run, sign-up, login and logout. ---
    # Users can be deleted; their lines stay (SET NULL), with the email kept as written.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.audit_log (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        at timestamptz NOT NULL DEFAULT now(),
        action text NOT NULL
            CHECK (action IN ('ask', 'rerun', 'signup', 'login', 'login_failed', 'logout')),
        outcome text NOT NULL CHECK (char_length(outcome) BETWEEN 1 AND 50),
        user_id uuid REFERENCES {SCHEMA}.users (id) ON DELETE SET NULL,
        workspace_id uuid REFERENCES {SCHEMA}.workspaces (id) ON DELETE SET NULL,
        email text,
        conversation_id uuid,
        question text,
        sql text,
        tables text[] NOT NULL DEFAULT '{{}}',
        row_count integer,
        duration_ms integer,
        sql_attempts integer,
        detail text
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS audit_log_per_workspace
        ON {SCHEMA}.audit_log (workspace_id, id DESC)
    """,
    # --- The secrets store (5.7): users' LLM keys and database passwords, encrypted. ---
    # `ciphertext` is a Fernet token bound to (workspace, id): see src/querymind/secrets/cipher.py.
    # No DEFAULT on id: the app makes it before encrypting. `hint` = an LLM key's last 4 characters.
    f"""
    CREATE TABLE IF NOT EXISTS {SCHEMA}.secrets (
        id uuid PRIMARY KEY,
        workspace_id uuid NOT NULL REFERENCES {SCHEMA}.workspaces (id) ON DELETE CASCADE,
        kind text NOT NULL CHECK (kind IN ('llm_key', 'db_password')),
        ciphertext text NOT NULL,
        hint text CHECK (hint IS NULL OR (kind = 'llm_key' AND char_length(hint) = 4)),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
    )
    """,
    f"CREATE INDEX IF NOT EXISTS secrets_per_workspace ON {SCHEMA}.secrets (workspace_id)",
    # What the history list sorts by: one user's conversations.
    f"DROP INDEX IF EXISTS {SCHEMA}.conversations_list",
    f"""
    CREATE INDEX IF NOT EXISTS conversations_per_user
        ON {SCHEMA}.conversations (user_id, pinned DESC, updated_at DESC)
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
        # The audit log is append-only for the app (5.5): lines can be added, never changed.
        sql.SQL("REVOKE UPDATE, DELETE ON {} FROM {}").format(
            sql.Identifier(SCHEMA, "audit_log"), role
        ),
    ]
    for statement in statements:
        conn.execute(statement)


def give_unowned_rows_to(conn: psycopg.Connection, email: str) -> int:
    """Give conversations and saved questions saved before 5.1 (no owner) to the account with
    `email`, in its first workspace. Returns how many rows changed.

    Never automatic: with open sign-up, "the first user" could be anyone.

    Raises:
        ConfigError: no account has that email (sign up first).
    """
    owner = conn.execute(
        f"""
        SELECT u.id, m.workspace_id FROM {SCHEMA}.users AS u
        JOIN {SCHEMA}.memberships AS m ON m.user_id = u.id
        WHERE u.email = %s ORDER BY m.created_at, m.workspace_id LIMIT 1
        """.encode(),
        (email.lower(),),
    ).fetchone()
    if owner is None:
        raise ConfigError(f"No account for {email!r}: sign up in the app first, then run it again")
    changed = 0
    for table in ("conversations", "saved_questions"):
        cursor = conn.execute(
            sql.SQL("UPDATE {} SET user_id = %s, workspace_id = %s WHERE user_id IS NULL").format(
                sql.Identifier(SCHEMA, table)
            ),
            owner,
        )
        changed += cursor.rowcount
    return changed


def main() -> int:
    """Set up the app's storage. Returns a process exit code (0 = success)."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Create or update the app's own storage.")
    parser.add_argument(
        "--owner",
        metavar="EMAIL",
        help="give conversations saved before logins existed to this account (sign up first)",
    )
    args = parser.parse_args()
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
            if args.owner:
                changed = give_unowned_rows_to(conn, args.owner)
                logger.info("Gave %d unowned rows to %s.", changed, args.owner)
    except (ConfigError, psycopg.Error) as exc:
        logger.error("Setting up the app's storage failed: %s", exc)
        return 1
    logger.info("App storage is ready: schema %r, role %r.", SCHEMA, settings.app_db_user)
    return 0


if __name__ == "__main__":
    sys.exit(main())
