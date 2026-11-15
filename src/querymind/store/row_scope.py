"""Row-level security (5.3): the rows each member of a workspace may see.

    store = RowScopeStore(settings)
    store.scope(workspace_id, user_id, "viewer")      # ("Eastern",), or None = every row
    store.member(workspace_id, user_id)               # ("viewer", ("Eastern",)) or None
    store.set(workspace_id, user_id, ("Eastern",))    # None = every row; False: not a member

The scope is a list of tags on the membership (`querymind_app.memberships.row_scope`), set by an
admin (api/row_scope.py). The executor hands it to the database for every query, and the
database's own policies decide which rows that means (safety/row_scope.py). Admins always see
every row.

Fresh for every question (not from the login token), so an admin's change applies at once. Fail
closed: when it can't be read, `scope` raises (the question is refused with a 503); it never
answers "every row".
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

import psycopg

from querymind.auth.tokens import Role
from querymind.config import Settings
from querymind.db.connection import AppConnect, connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.safety.row_scope import RowScope
from querymind.store.errors import StoreUnavailableError

logger = logging.getLogger(__name__)


class RowScopeStore:
    """Reads and writes the `row_scope` of `querymind_app.memberships`."""

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
            logger.warning("row scope store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Row permissions can't be read right now.") from exc

    def scope(self, workspace_id: UUID, user_id: UUID, role: Role) -> RowScope:
        """The rows this user may see: None = every row (no scope set, or an admin). No longer a
        member (a token still valid for a few minutes): `()`, no row at all.

        Raises:
            StoreUnavailableError: it can't be read. Never read as "every row".
        """
        if role == "admin":
            return None
        found = self.member(workspace_id, user_id)
        return () if found is None else found[1]

    def member(self, workspace_id: UUID, user_id: UUID) -> tuple[Role, RowScope] | None:
        """A member's role and scope, or None if they aren't a member of this workspace."""
        with self._transaction() as conn:
            row = conn.execute(
                """
                SELECT role, row_scope FROM querymind_app.memberships
                WHERE workspace_id = %s AND user_id = %s
                """,
                (workspace_id, user_id),
            ).fetchone()
        if row is None:
            return None
        role, tags = row
        return role, (None if tags is None else tuple(tags))

    def set(self, workspace_id: UUID, user_id: UUID, scope: RowScope) -> bool:
        """Give a member a scope (None = every row). False if they aren't a member here.

        The caller checks the tags (`safety.row_scope.checked`) and that the member isn't an admin.
        """
        with self._transaction() as conn:
            updated = conn.execute(
                """
                UPDATE querymind_app.memberships SET row_scope = %s
                WHERE workspace_id = %s AND user_id = %s
                """,
                (None if scope is None else list(scope), workspace_id, user_id),
            )
            return updated.rowcount == 1
