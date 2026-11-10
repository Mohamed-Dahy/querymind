"""A workspace's own databases (5.9, ADR 012): where each one is and how to log in.

    store = ConnectionStore(settings, secrets)
    record = store.save(workspace_id, "Sales", spec, SecretStr("..."))   # after the checks
    store.list(workspace_id)               # never the password
    store.password(workspace_id, record)   # server-side only, to connect
    store.delete(workspace_id, record.id)

Several per workspace, each with a unique name. The password is a secret (5.7, kind
`db_password`, never shown in any form); this table holds the rest. Saving happens only after
`db/remote.py` has connected and found the login read-only: the route does that first.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg import errors
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict, SecretStr

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.remote import ConnectionSpec, SslMode
from querymind.secrets.errors import SecretNotFoundError
from querymind.secrets.store import SecretStore
from querymind.store.errors import (
    ConnectionNameTakenError,
    ConnectionNotFoundError,
    StoreError,
    StoreUnavailableError,
)

logger = logging.getLogger(__name__)

_COLUMNS = "id, name, host, port, dbname, username, sslmode, secret_id, checked_at, created_at"
_NOT_FOUND = "No database connection with this id."


class ConnectionRecord(BaseModel):
    """A saved connection, without its password."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    name: str
    host: str
    port: int
    dbname: str
    username: str
    sslmode: SslMode
    secret_id: UUID
    checked_at: datetime  # the last time it was found reachable and read-only
    created_at: datetime

    def spec(self) -> ConnectionSpec:
        """Where to connect (checked against SSRF again at every connect)."""
        return ConnectionSpec(
            host=self.host,
            port=self.port,
            dbname=self.dbname,
            user=self.username,
            sslmode=self.sslmode,
        )


class ConnectionStore:
    """Reads and writes `querymind_app.connections`; passwords go to the secrets store."""

    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        *,
        connect: Callable[[Settings], psycopg.Connection] = connect_app,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("connection store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Saved databases aren't available right now.") from exc

    def save(
        self, workspace_id: UUID, name: str, spec: ConnectionSpec, password: SecretStr
    ) -> ConnectionRecord:
        """Save a checked connection. Its password is encrypted in the secrets store.

        Raises:
            ConnectionNameTakenError: the workspace already has one with this name.
        """
        secret = self._secrets.save(workspace_id, "db_password", password)
        try:
            with self._transaction() as conn:
                record = (
                    conn.cursor(row_factory=class_row(ConnectionRecord))
                    .execute(
                        f"""
                        INSERT INTO querymind_app.connections
                            (workspace_id, name, host, port, dbname, username, sslmode, secret_id,
                             checked_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                        RETURNING {_COLUMNS}
                        """.encode(),
                        (
                            workspace_id,
                            name,
                            spec.host,
                            spec.port,
                            spec.dbname,
                            spec.user,
                            spec.sslmode,
                            secret.id,
                        ),
                    )
                    .fetchone()
                )
        except StoreUnavailableError as exc:
            self._forget(workspace_id, secret.id)  # no orphan password left behind
            if isinstance(exc.__cause__, errors.UniqueViolation):
                raise ConnectionNameTakenError(
                    f"This workspace already has a database called {name!r}."
                ) from None
            raise
        assert record is not None  # INSERT ... RETURNING always gives the row
        logger.info(
            "connection saved",
            extra={"workspace_id": str(workspace_id), "connection_id": str(record.id)},
        )
        return record

    def list(self, workspace_id: UUID) -> list[ConnectionRecord]:
        """The workspace's databases, by name."""
        with self._transaction() as conn:
            return (
                conn.cursor(row_factory=class_row(ConnectionRecord))
                .execute(
                    f"SELECT {_COLUMNS} FROM querymind_app.connections"
                    " WHERE workspace_id = %s ORDER BY name".encode(),
                    (workspace_id,),
                )
                .fetchall()
            )

    def get(self, workspace_id: UUID, connection_id: UUID) -> ConnectionRecord:
        """One of the workspace's databases.

        Raises:
            ConnectionNotFoundError: not this workspace's (or no such id).
        """
        with self._transaction() as conn:
            record = (
                conn.cursor(row_factory=class_row(ConnectionRecord))
                .execute(
                    f"SELECT {_COLUMNS} FROM querymind_app.connections"
                    " WHERE id = %s AND workspace_id = %s".encode(),
                    (connection_id, workspace_id),
                )
                .fetchone()
            )
        if record is None:
            raise ConnectionNotFoundError(_NOT_FOUND)
        return record

    def password(self, workspace_id: UUID, record: ConnectionRecord) -> SecretStr:
        """The decrypted password, to connect. Never put it in a reply or a log line."""
        return self._secrets.reveal(workspace_id, record.secret_id)

    def mark_checked(self, workspace_id: UUID, connection_id: UUID) -> ConnectionRecord:
        """Record that it was just found reachable and read-only again."""
        with self._transaction() as conn:
            record = (
                conn.cursor(row_factory=class_row(ConnectionRecord))
                .execute(
                    f"""
                    UPDATE querymind_app.connections SET checked_at = now()
                    WHERE id = %s AND workspace_id = %s
                    RETURNING {_COLUMNS}
                    """.encode(),
                    (connection_id, workspace_id),
                )
                .fetchone()
            )
        if record is None:
            raise ConnectionNotFoundError(_NOT_FOUND)
        return record

    def delete(self, workspace_id: UUID, connection_id: UUID) -> None:
        """Remove it and its password.

        Raises:
            ConnectionNotFoundError: not this workspace's (or no such id).
        """
        record = self.get(workspace_id, connection_id)
        try:
            # Deleting the secret deletes the connection too (ON DELETE CASCADE).
            self._secrets.delete(workspace_id, record.secret_id)
        except SecretNotFoundError:
            raise ConnectionNotFoundError(_NOT_FOUND) from None  # deleted meanwhile
        logger.info(
            "connection deleted",
            extra={"workspace_id": str(workspace_id), "connection_id": str(connection_id)},
        )

    def _forget(self, workspace_id: UUID, secret_id: UUID) -> None:
        """Delete a password nothing points to. Best effort: a failure is logged, not raised."""
        try:
            self._secrets.delete(workspace_id, secret_id)
        except StoreError:
            logger.warning(
                "unused connection password not deleted",
                extra={"workspace_id": str(workspace_id), "secret_id": str(secret_id)},
            )
