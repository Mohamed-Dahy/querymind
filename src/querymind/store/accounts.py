"""User accounts, workspaces and refresh tokens (5.1), in the app's own schema.

    accounts = AccountStore(settings)
    account = accounts.sign_up("ana@example.com", password_hash, workspace_name="Ana's workspace")
    accounts.find_login("ana@example.com")        # -> (Account, password hash) or None

A user belongs to workspaces through a membership, which carries the role (admin, analyst,
viewer). Sign-up creates the user, their own workspace and an admin membership in one transaction.
Workspaces own conversations now, and keys and database connections later (5.8, 5.9).

Refresh tokens are kept only as hashes, in families: one family per login. Each refresh marks the
old token used and adds a new one to the family. A used token that comes back means it was copied:
the whole family is revoked, which logs out the thief and the user alike (see auth/tokens.py).

Like the conversation store: one connection per call, every value passed as a parameter.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict

from querymind.auth.errors import EmailTakenError, NotAuthenticatedError
from querymind.auth.tokens import AuthUser, Role
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.store.errors import AccountsUnavailableError

logger = logging.getLogger(__name__)

# Two tabs refreshing at the same moment send the same cookie twice. Within this many seconds
# of its first use, a second use is treated as that race, not as a stolen token.
REUSE_GRACE_SECONDS = 10

_ACCOUNT_COLUMNS = """
    u.id AS user_id, u.email, w.id AS workspace_id, w.name AS workspace_name, m.role
"""


class Account(BaseModel):
    """A user in one workspace, with their role there: who a token is issued for."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str
    workspace_id: UUID
    workspace_name: str
    role: Role

    def auth_user(self) -> AuthUser:
        return AuthUser(
            user_id=self.user_id, workspace_id=self.workspace_id, role=self.role, email=self.email
        )


class _Login(Account):
    password_hash: str


class _RefreshRow(BaseModel):
    user_id: UUID
    workspace_id: UUID
    family_id: UUID
    dead: bool  # revoked or expired
    used_at: datetime | None
    recently_used: bool  # used within REUSE_GRACE_SECONDS


class AccountStore:
    """Reads and writes accounts. Raises AccountsUnavailableError when the database fails."""

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
        """A connection for one transaction: committed at the end, rolled back on an error."""
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("account store failed", extra={"detail": str(exc)})
            raise AccountsUnavailableError("Accounts aren't available right now.") from exc

    def sign_up(self, email: str, password_hash: str, *, workspace_name: str) -> Account:
        """A new user, their own workspace, and an admin membership, all or nothing.

        Raises:
            EmailTakenError: the email already has an account.
        """
        with self._transaction() as conn:
            try:
                user = conn.execute(
                    """
                    INSERT INTO querymind_app.users (email, password_hash) VALUES (%s, %s)
                    RETURNING id
                    """,
                    (email, password_hash),
                ).fetchone()
            except errors.UniqueViolation:
                # Raised inside the `with`, so the transaction is rolled back.
                raise EmailTakenError("An account with this email already exists.") from None
            workspace = conn.execute(
                "INSERT INTO querymind_app.workspaces (name) VALUES (%s) RETURNING id",
                (workspace_name,),
            ).fetchone()
            if user is None or workspace is None:  # INSERT … RETURNING always gives its row
                raise AccountsUnavailableError("The account wasn't created.")
            conn.execute(
                """
                INSERT INTO querymind_app.memberships (workspace_id, user_id, role)
                VALUES (%s, %s, 'admin')
                """,
                (workspace[0], user[0]),
            )
        return Account(
            user_id=user[0],
            email=email,
            workspace_id=workspace[0],
            workspace_name=workspace_name,
            role="admin",
        )

    def find_login(self, email: str) -> tuple[Account, str] | None:
        """The account for a login and its password hash, in the user's first workspace. None if
        there is no such (enabled) user."""
        query = f"""
            SELECT {_ACCOUNT_COLUMNS}, u.password_hash
            FROM querymind_app.users AS u
            JOIN LATERAL (
                SELECT workspace_id, role FROM querymind_app.memberships
                WHERE user_id = u.id ORDER BY created_at, workspace_id LIMIT 1
            ) AS m ON true
            JOIN querymind_app.workspaces AS w ON w.id = m.workspace_id
            WHERE u.email = %s AND NOT u.disabled
        """
        with self._transaction() as conn:
            found = (
                conn.cursor(row_factory=class_row(_Login))
                .execute(query.encode(), (email,))
                .fetchone()
            )
        if found is None:
            return None
        account = Account.model_validate(found.model_dump(exclude={"password_hash"}))
        return account, found.password_hash

    def account(self, user_id: UUID, workspace_id: UUID) -> Account | None:
        """The user in that workspace, with their current role; None if gone, disabled or no
        longer a member."""
        with self._transaction() as conn:
            return self._account(conn, user_id, workspace_id)

    def set_password_hash(self, user_id: UUID, password_hash: str) -> None:
        """Replace a hash, e.g. one made with older Argon2 settings."""
        with self._transaction() as conn:
            conn.execute(
                "UPDATE querymind_app.users SET password_hash = %s WHERE id = %s",
                (password_hash, user_id),
            )

    def start_session(self, account: Account, token_hash: str, expires_at: datetime) -> None:
        """A login: the first refresh token of a new family. Also clears this user's old,
        long-expired tokens."""
        with self._transaction() as conn:
            conn.execute(
                """
                DELETE FROM querymind_app.refresh_tokens
                WHERE user_id = %s AND expires_at < now() - interval '1 day'
                """,
                (account.user_id,),
            )
            self._add_token(conn, account, uuid4(), token_hash, expires_at)

    def rotate(self, token_hash: str, new_hash: str, expires_at: datetime) -> Account:
        """Use a refresh token: mark it used, add `new_hash` to its family, return the account.

        Raises:
            NotAuthenticatedError: unknown, expired or revoked token; or a used one (then the
                whole family is revoked first, and that is committed).
        """
        with self._transaction() as conn:
            outcome, account = self._rotate(conn, token_hash, new_hash, expires_at)
        # Raised after the transaction committed, so a revoked family stays revoked.
        if outcome == "reused":
            logger.warning("refresh token reused: family revoked")
        if account is None:
            raise NotAuthenticatedError("Please log in.")
        return account

    def end_session(self, token_hash: str) -> None:
        """Log out: revoke the whole family. An unknown token is fine (logged out already)."""
        with self._transaction() as conn:
            conn.execute(
                """
                UPDATE querymind_app.refresh_tokens SET revoked_at = now()
                WHERE revoked_at IS NULL AND family_id = (
                    SELECT family_id FROM querymind_app.refresh_tokens WHERE token_hash = %s
                )
                """,
                (token_hash,),
            )

    def _rotate(
        self, conn: psycopg.Connection, token_hash: str, new_hash: str, expires_at: datetime
    ) -> tuple[Literal["ok", "unknown", "dead", "reused"], Account | None]:
        # FOR UPDATE: two refreshes with one token run one after the other, never both "unused".
        row = (
            conn.cursor(row_factory=class_row(_RefreshRow))
            .execute(
                f"""
                SELECT user_id, workspace_id, family_id, used_at,
                       revoked_at IS NOT NULL OR expires_at <= now() AS dead,
                       COALESCE(used_at > now() - interval '{REUSE_GRACE_SECONDS} seconds', false)
                           AS recently_used  -- never used: NULL > … is NULL, so say false
                FROM querymind_app.refresh_tokens WHERE token_hash = %s FOR UPDATE
                """.encode(),
                (token_hash,),
            )
            .fetchone()
        )
        if row is None:
            return "unknown", None
        if row.dead:
            return "dead", None
        if row.used_at is not None and not row.recently_used:
            self._revoke_family(conn, row.family_id)
            return "reused", None
        account = self._account(conn, row.user_id, row.workspace_id)
        if account is None:  # deleted, disabled, or removed from the workspace since
            self._revoke_family(conn, row.family_id)
            return "dead", None
        conn.execute(
            """
            UPDATE querymind_app.refresh_tokens SET used_at = COALESCE(used_at, now())
            WHERE token_hash = %s
            """,
            (token_hash,),
        )
        self._add_token(conn, account, row.family_id, new_hash, expires_at)
        return "ok", account

    @staticmethod
    def _account(conn: psycopg.Connection, user_id: UUID, workspace_id: UUID) -> Account | None:
        query = f"""
            SELECT {_ACCOUNT_COLUMNS}
            FROM querymind_app.users AS u
            JOIN querymind_app.memberships AS m ON m.user_id = u.id
            JOIN querymind_app.workspaces AS w ON w.id = m.workspace_id
            WHERE u.id = %s AND w.id = %s AND NOT u.disabled
        """
        return (
            conn.cursor(row_factory=class_row(Account))
            .execute(query.encode(), (user_id, workspace_id))
            .fetchone()
        )

    @staticmethod
    def _add_token(
        conn: psycopg.Connection,
        account: Account,
        family_id: UUID,
        token_hash: str,
        expires_at: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO querymind_app.refresh_tokens
                (user_id, workspace_id, family_id, token_hash, expires_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (account.user_id, account.workspace_id, family_id, token_hash, expires_at),
        )

    @staticmethod
    def _revoke_family(conn: psycopg.Connection, family_id: UUID) -> None:
        conn.execute(
            """
            UPDATE querymind_app.refresh_tokens SET revoked_at = now()
            WHERE family_id = %s AND revoked_at IS NULL
            """,
            (family_id,),
        )


def create_account_store(settings: Settings) -> AccountStore | None:
    """The account store, or None when the app's role isn't configured."""
    return AccountStore(settings) if settings.app_db_user is not None else None
