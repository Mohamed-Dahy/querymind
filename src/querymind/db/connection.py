"""The one place the app opens database connections.

Every agent query goes through `connect_readonly`, so it always runs as the read-only role
(analyst_ro). Its sessions start with `querymind.scope = *` (every row, 5.3), for what the app
reads for everyone (the schema and its sample rows); the executor sets each query's own scope
anyway. Saved conversations go through `connect_app`, as a role that can't see business data;
the API reuses those through `AppPool` (Step 0), scripts and evals open their own.
The admin connection is for setup scripts only and never used here.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager

import psycopg
from psycopg_pool import ConnectionPool

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


# Anything that opens an app-database connection for one `with` block: commit at the end, roll
# back on an error. `connect_app` (a new connection, closed after) and `AppPool.connect` (a pooled
# one, given back after) both fit, so every store takes it as `connect=`.
AppConnect = Callable[[Settings], AbstractContextManager[psycopg.Connection]]


class AppPool:
    """A pool of open connections as the app's role, shared by the API's stores (Step 0, ADR 017).

        pool = AppPool(settings); pool.open()        # at startup
        AccountStore(settings, connect=pool.connect)
        pool.close()                                 # at shutdown

    Opening a connection costs ~35-50 ms here (TCP + password check); a pooled one ~1 ms. Since
    5.12 every logged-in request reads the membership, so this is on every request's path.

    `connect` behaves like `with connect_app(settings) as conn:`: one transaction, committed when
    the block ends, rolled back on an error; the connection then goes back to the pool instead of
    being closed. Each one is checked on the way out (`check_connection`), so a database restart
    costs one reconnect, not an error. All busy (or the database down) for `timeout` seconds:
    `PoolTimeout`, a `psycopg.OperationalError`, which the stores already turn into their 503.
    """

    def __init__(self, settings: Settings, *, min_size: int, max_size: int, timeout: float) -> None:
        self._pool = ConnectionPool(
            settings.app_conninfo(),
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            check=ConnectionPool.check_connection,
            name="querymind-app",
            open=False,  # opened by the app's startup, not on import or in tests
        )

    def open(self) -> None:
        """Start filling the pool. Doesn't wait: the API starts even if the database is still
        coming up, and requests wait (up to `timeout`) for a connection."""
        self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    def connect(self, settings: Settings) -> AbstractContextManager[psycopg.Connection]:
        """A pooled connection for one `with` block (`settings` is ignored: the pool was built
        with them; the parameter keeps the `AppConnect` shape)."""
        return self._pool.connection()
