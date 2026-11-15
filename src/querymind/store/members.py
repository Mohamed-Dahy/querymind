"""Workspace members (5.12): who belongs to a workspace, with which role.

    members = MemberStore(settings)
    members.list(workspace_id)                          # every member, oldest first
    members.add(workspace_id, "ana@example.com", "viewer")
    members.set_role(workspace_id, user_id, "analyst")  # -> (the member now, their old role)
    members.remove(workspace_id, user_id)               # -> the member as they were

Only an admin calls these (api/members.py). A member is a row of `querymind_app.memberships`; the
person must have signed up already (no invitations). The role decides the tables (5.2), the
masking (5.4) and every-row access (5.3), and the API reads it fresh on every request, so a change
here applies at the member's next request.

**A workspace always keeps an admin.** Demoting or removing the last (enabled) admin is refused,
yourself included. Two admins demoting each other at the same moment could each see "there's
another admin" and both succeed; so each change first locks the workspace's admin rows
(`SELECT … FOR UPDATE`), and the second one waits, then counts again.

Like the other stores: one connection per call, every value passed as a parameter.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from querymind.auth.tokens import Role
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.store.errors import (
    AccountNotFoundError,
    AlreadyMemberError,
    LastAdminError,
    MemberNotFoundError,
    StoreUnavailableError,
)

logger = logging.getLogger(__name__)

_MEMBER = """
    SELECT m.user_id, u.email, m.role, m.row_scope, m.created_at AS added_at
    FROM querymind_app.memberships AS m
    JOIN querymind_app.users AS u ON u.id = m.user_id
"""


class Member(BaseModel):
    """A member of a workspace: their account, role, rows (5.3) and when they joined."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str
    role: Role
    row_scope: tuple[str, ...] | None  # None = every row (and always for an admin)
    added_at: datetime


class MemberStore:
    """Reads and changes a workspace's memberships. Raises StoreUnavailableError when the
    database fails."""

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
        """One transaction: committed at the end, rolled back on any error (ours included)."""
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("member store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Members can't be read or changed right now.") from exc

    def list(self, workspace_id: UUID) -> list[Member]:
        """Every member of the workspace, in the order they joined."""
        with self._transaction() as conn:
            return (
                conn.cursor(row_factory=class_row(Member))
                .execute(
                    f"{_MEMBER} WHERE m.workspace_id = %s ORDER BY m.created_at, u.email".encode(),
                    (workspace_id,),
                )
                .fetchall()
            )

    def add(self, workspace_id: UUID, email: str, role: Role) -> Member:
        """Add a signed-up user to the workspace with `role` (and every row: no scope yet).

        Raises:
            AccountNotFoundError: no enabled account has this email (they sign up first).
            AlreadyMemberError: they are a member already (their role is changed with set_role).
        """
        with self._transaction() as conn:
            user = conn.execute(
                "SELECT id FROM querymind_app.users WHERE email = %s AND NOT disabled",
                (email.lower(),),
            ).fetchone()
            if user is None:
                raise AccountNotFoundError("No account has this email: they need to sign up first.")
            # DO NOTHING + no row back = the membership was there already (also when two admins
            # add the same person at once: the second one is told, nothing breaks).
            added = conn.execute(
                """
                INSERT INTO querymind_app.memberships (workspace_id, user_id, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (workspace_id, user_id) DO NOTHING
                RETURNING user_id
                """,
                (workspace_id, user[0], role),
            ).fetchone()
            if added is None:
                raise AlreadyMemberError("They are already a member of this workspace.")
            member = self._member(conn, workspace_id, user[0], lock=False)
        if member is None:  # the row this transaction just wrote is always there
            raise StoreUnavailableError("The member wasn't added.")
        return member

    def set_role(self, workspace_id: UUID, user_id: UUID, role: Role) -> tuple[Member, Role]:
        """Give a member another role. Returns the member now and their role before.

        Promoted to admin, their row scope is cleared (admins see every row); demoted from admin,
        they start with every row too, until an admin sets a scope.

        Raises:
            MemberNotFoundError: not a member of this workspace.
            LastAdminError: they are its last admin and `role` isn't admin.
        """
        with self._transaction() as conn:
            before = self._guarded(conn, workspace_id, user_id, keeps_admin=role == "admin")
            conn.execute(
                """
                UPDATE querymind_app.memberships
                SET role = %(role)s,
                    row_scope = CASE WHEN %(role)s = 'admin' THEN NULL ELSE row_scope END
                WHERE workspace_id = %(workspace)s AND user_id = %(user)s
                """,
                {"role": role, "workspace": workspace_id, "user": user_id},
            )
            after = self._member(conn, workspace_id, user_id, lock=False)
        if after is None:  # locked by this transaction since `_guarded`: it can't have gone
            raise StoreUnavailableError("The role wasn't changed.")
        return after, before.role

    def remove(self, workspace_id: UUID, user_id: UUID) -> Member:
        """Take a member out of the workspace. Returns them as they were (for the audit log).

        Their conversations in this workspace stay (they are theirs); added again, they see them
        again. Their next request is refused (the API reads the membership every time) and their
        next refresh takes them to their own workspace (store/accounts.py).

        Raises:
            MemberNotFoundError: not a member of this workspace.
            LastAdminError: they are its last admin.
        """
        with self._transaction() as conn:
            member = self._guarded(conn, workspace_id, user_id, keeps_admin=False)
            conn.execute(
                "DELETE FROM querymind_app.memberships WHERE workspace_id = %s AND user_id = %s",
                (workspace_id, user_id),
            )
        return member

    def _guarded(
        self, conn: psycopg.Connection, workspace_id: UUID, user_id: UUID, *, keeps_admin: bool
    ) -> Member:
        """Lock the workspace's admins and the member, then refuse a change that leaves no admin.

        `keeps_admin`: after the change, the member is (still) an admin.
        """
        # Every change locks the admin rows first, always in the same order (by user id), so two
        # changes at once wait for each other instead of deadlocking. After the wait, Postgres
        # checks `role = 'admin'` again on the row as the other change left it.
        admins = {
            row[0]
            for row in conn.execute(
                """
                SELECT m.user_id FROM querymind_app.memberships AS m
                JOIN querymind_app.users AS u ON u.id = m.user_id
                WHERE m.workspace_id = %s AND m.role = 'admin' AND NOT u.disabled
                ORDER BY m.user_id
                FOR UPDATE OF m
                """,
                (workspace_id,),
            ).fetchall()
        }
        member = self._member(conn, workspace_id, user_id, lock=True)
        if member is None:
            raise MemberNotFoundError("This workspace has no such member.")
        # Only a change that takes an admin away can leave none. A disabled admin can't log in,
        # so they don't count as "another admin".
        if member.role == "admin" and not keeps_admin and not admins - {user_id}:
            raise LastAdminError("A workspace needs an admin: make someone else an admin first.")
        return member

    @staticmethod
    def _member(
        conn: psycopg.Connection, workspace_id: UUID, user_id: UUID, *, lock: bool
    ) -> Member | None:
        lock_clause = "FOR UPDATE OF m" if lock else ""
        return (
            conn.cursor(row_factory=class_row(Member))
            .execute(
                f"{_MEMBER} WHERE m.workspace_id = %s AND m.user_id = %s {lock_clause}".encode(),
                (workspace_id, user_id),
            )
            .fetchone()
        )
