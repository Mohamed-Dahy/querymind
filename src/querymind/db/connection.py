"""The one place the app opens database connections.

Every agent query goes through `connect_readonly`, so it always runs as the read-only role
(analyst_ro). Its sessions start with `querymind.scope = *` (every row, 5.3), for what the app
reads for everyone (the schema and its sample rows); the executor sets each query's own scope
anyway. Saved conversations go through `connect_app`, as a role that can't see business data.
The admin connection is for setup scripts only and never used here.
"""

import logging

import psycopg

from querymind.config import Settings
from querymind.db.errors import DatabaseUnavailableError
from querymind.safety.row_scope import EVERY_ROW, SCOPE_SETTING

logger = logging.getLogger(__name__)


def connect_readonly(settings: Settings) -> psycopg.Connection:
    """Open a new connection as the read-only role. The caller must close it (use `with`).

    Raises:
        DatabaseUnavailableError: the connection failed. Its message is safe to show users;
            psycopg's own message (host, port, user) is logged and kept as `__cause__`.
    """
    try:
        # `-c name=value`: a session setting, as if SET at the start (safety/row_scope.py).
        return psycopg.connect(
            settings.readonly_conninfo(), options=f"-c {SCOPE_SETTING}={EVERY_ROW}"
        )
    except psycopg.OperationalError as exc:
        logger.warning("database connection failed", extra={"detail": str(exc)})
        raise DatabaseUnavailableError(
            "Could not connect to the database. Please try again later."
        ) from exc


def connect_app(settings: Settings) -> psycopg.Connection:
    """Open a new connection as the app's role. The caller must close it (use `with`).

    Raises:
        DatabaseUnavailableError: the connection failed; see `connect_readonly`.
    """
    try:
        return psycopg.connect(settings.app_conninfo())
    except psycopg.OperationalError as exc:
        logger.warning("app database connection failed", extra={"detail": str(exc)})
        raise DatabaseUnavailableError("Could not connect to the app's database.") from exc
