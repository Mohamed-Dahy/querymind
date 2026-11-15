"""Table permissions (5.2): which tables a role may use, per workspace and database.

    store = TableAccessStore(settings)
    store.allowed(workspace_id, connection_id, "viewer")   # None = every table; else only these
    store.rules(workspace_id, connection_id)               # {"analyst": None, "viewer": [...]}
    store.set(workspace_id, connection_id, "viewer", ["orders", "products"])   # None = every table

One row per (workspace, database, role) in `querymind_app.table_access`; `connection_id` NULL is
the shared demo database (a workspace's rule for it holds only inside that workspace). No row
means every table. A row lists the tables the role may use: a table added to the database later
stays hidden until an admin grants it (decided with Mohamed). Admins are never restricted.

Fail closed: when the rules can't be read, `allowed` raises (the question is refused with a 503).
It never answers "no rule", which would mean every table.
"""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal
from uuid import UUID

import psycopg

from querymind.auth.tokens import Role
from querymind.config import Settings
from querymind.db.connection import AppConnect, connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.store.errors import StoreUnavailableError

logger = logging.getLogger(__name__)

RestrictedRole = Literal["analyst", "viewer"]
RESTRICTABLE: tuple[RestrictedRole, ...] = ("analyst", "viewer")


class TableAccessStore:
    """Reads and writes `querymind_app.table_access`."""

    def __init__(
        self,
        settings: Settings,
        *,
        connect: AppConnect = connect_app,
    ) -> None:
        self._settings = settings
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("table access store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Table permissions can't be read right now.") from exc

    def allowed(
        self, workspace_id: UUID, connection_id: UUID | None, role: Role
    ) -> frozenset[str] | None:
        """The tables this role may use on this database: None = every table (no rule, or admin).

        Raises:
            StoreUnavailableError: the rules can't be read. Never read as "no rule".
        """
        if role == "admin":
            return None
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT tables FROM querymind_app.table_access
                WHERE workspace_id = %s AND connection_id IS NOT DISTINCT FROM %s AND role = %s
                """,
                (workspace_id, connection_id, role),
            ).fetchone()
        return None if row is None else frozenset(row[0])

    def rules(
        self, workspace_id: UUID, connection_id: UUID | None
    ) -> dict[RestrictedRole, list[str] | None]:
        """Each restrictable role's tables on this database (None = every table)."""
        with self._transaction() as conn:
            rows = conn.execute(
                """
                SELECT role, tables FROM querymind_app.table_access
                WHERE workspace_id = %s AND connection_id IS NOT DISTINCT FROM %s
                """,
                (workspace_id, connection_id),
            ).fetchall()
        found = {role: sorted(tables) for role, tables in rows}
        return {role: found.get(role) for role in RESTRICTABLE}

    def set(
        self,
        workspace_id: UUID,
        connection_id: UUID | None,
        role: RestrictedRole,
        tables: Sequence[str] | None,
    ) -> None:
        """Restrict `role` to `tables` on this database; None lifts the rule (every table).

        The caller checks that the database is this workspace's and that the tables exist.
        """
        with self._transaction() as conn:
            if tables is None:
                conn.execute(
                    """
                    DELETE FROM querymind_app.table_access
                    WHERE workspace_id = %s AND connection_id IS NOT DISTINCT FROM %s
                        AND role = %s
                    """,
                    (workspace_id, connection_id, role),
                )
                return
            conn.execute(
                """
                INSERT INTO querymind_app.table_access (workspace_id, connection_id, role, tables)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (workspace_id, connection_id, role)
                DO UPDATE SET tables = EXCLUDED.tables, updated_at = now()
                """,
                (workspace_id, connection_id, role, sorted(set(tables))),
            )
