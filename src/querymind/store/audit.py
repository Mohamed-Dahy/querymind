"""The audit log (5.5): who asked what, the SQL that ran, and how it ended, in the database.

    audit = AuditStore(settings)
    audit.record(AuditEntry(action="ask", outcome="answer", user_id=..., question=..., sql=...))
    audit.list(workspace_id, outcome="unsafe_sql")   # newest first, one workspace

One row per question, re-run, sign-up, login (and failed login) and logout. Rows are never
changed: the app's role may only INSERT and SELECT this table (data/setup_app_db.py revokes UPDATE
and DELETE), so a bug or an attacker using the app's connection can add lines but can't rewrite
history.

What is kept: the question and the SQL (they can quote values a user typed), never result rows.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.store.conversations import like_pattern
from querymind.store.errors import StoreUnavailableError

logger = logging.getLogger(__name__)

Action = Literal[
    "ask",
    "rerun",
    "signup",
    "login",
    "login_failed",
    "logout",
    "llm_key_saved",
    "llm_key_deleted",
    "llm_key_refused",
    "connection_saved",
    "connection_tested",
    "connection_refused",
    "connection_deleted",
]

_COLUMNS = """
    id, at, action, outcome, user_id, workspace_id, email, conversation_id, connection_id, question,
    sql, tables,
    row_count, duration_ms, sql_attempts, detail
"""


class AuditEntry(BaseModel):
    """One line of the audit log, before it is written."""

    model_config = ConfigDict(frozen=True)

    action: Action
    # answer / clarification / refusal / ok / cancelled, or the API's error code (unsafe_sql…).
    outcome: str
    user_id: UUID | None = None  # None: a failed login with an unknown email
    workspace_id: UUID | None = None
    email: str | None = None  # as typed on a failed login; the user's on everything else
    conversation_id: UUID | None = None
    connection_id: UUID | None = None  # the database it ran on (5.11); None = the demo
    question: str | None = None
    sql: str | None = None  # the validated SQL that ran (or the last one tried)
    tables: tuple[str, ...] = ()
    row_count: int | None = None
    duration_ms: int | None = None
    sql_attempts: int | None = None
    detail: str | None = None  # an error's user-facing message, never a vendor's raw reply


class AuditRecord(AuditEntry):
    """A written line: its number and time."""

    id: int
    at: datetime


class AuditStore:
    """Writes and reads the audit log. Raises StoreUnavailableError when the database fails."""

    def __init__(
        self,
        settings: Settings,
        *,
        connect: Callable[[Settings], psycopg.Connection] = connect_app,
    ) -> None:
        self._settings = settings
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("audit store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("The audit log isn't available right now.") from exc

    def record(self, entry: AuditEntry) -> None:
        """Add one line."""
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO querymind_app.audit_log
                    (action, outcome, user_id, workspace_id, email, conversation_id,
                     connection_id, question, sql, tables, row_count, duration_ms, sql_attempts,
                     detail)
                VALUES (%(action)s, %(outcome)s, %(user_id)s, %(workspace_id)s, %(email)s,
                        %(conversation_id)s, %(connection_id)s, %(question)s, %(sql)s, %(tables)s,
                        %(row_count)s, %(duration_ms)s, %(sql_attempts)s, %(detail)s)
                """,
                {**entry.model_dump(), "tables": list(entry.tables)},
            )

    def list(
        self,
        workspace_id: UUID,
        *,
        action: Action | None = None,
        outcome: str | None = None,
        email: str | None = None,
        search: str | None = None,
        before: int | None = None,
        limit: int = 50,
    ) -> tuple[list[AuditRecord], bool]:
        """One workspace's lines, newest first, and whether there are more.

        `search` looks in the question and the SQL. `before` = the smallest id already shown: the
        next page starts there (stable even while new lines arrive, unlike an offset).
        """
        query = f"""
            SELECT {_COLUMNS} FROM querymind_app.audit_log
            WHERE workspace_id = %(workspace)s
              AND (%(action)s::text IS NULL OR action = %(action)s)
              AND (%(outcome)s::text IS NULL OR outcome = %(outcome)s)
              AND (%(email)s::text IS NULL OR email = %(email)s)
              AND (%(search)s::text IS NULL
                   OR question ILIKE %(like)s ESCAPE '\\'
                   OR sql ILIKE %(like)s ESCAPE '\\')
              AND (%(before)s::bigint IS NULL OR id < %(before)s)
            ORDER BY id DESC
            LIMIT %(limit)s
        """
        with self._transaction() as conn:
            found = (
                conn.cursor(row_factory=class_row(AuditRecord))
                .execute(
                    query.encode(),
                    {
                        "workspace": workspace_id,
                        "action": action,
                        "outcome": outcome,
                        "email": email,
                        "search": search,
                        "like": like_pattern(search or ""),
                        "before": before,
                        "limit": limit + 1,  # one extra says whether another page exists
                    },
                )
                .fetchall()
            )
        return found[:limit], len(found) > limit


def create_audit_store(settings: Settings) -> AuditStore | None:
    """The audit store, or None when the app's role isn't configured."""
    return AuditStore(settings) if settings.app_db_user is not None else None
