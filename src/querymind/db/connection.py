"""The one place the agent opens database connections.

Every agent query goes through `connect_readonly`, so it always runs as the read-only role
(analyst_ro). The admin connection is for setup scripts only and never used here.
"""

import logging

import psycopg

from querymind.config import Settings
from querymind.db.errors import DatabaseUnavailableError

logger = logging.getLogger(__name__)


def connect_readonly(settings: Settings) -> psycopg.Connection:
    """Open a new connection as the read-only role. The caller must close it (use `with`).

    Raises:
        DatabaseUnavailableError: the connection failed. Its message is safe to show users;
            psycopg's own message (host, port, user) is logged and kept as `__cause__`.
    """
    try:
        return psycopg.connect(settings.readonly_conninfo())
    except psycopg.OperationalError as exc:
        logger.warning("database connection failed", extra={"detail": str(exc)})
        raise DatabaseUnavailableError(
            "Could not connect to the database. Please try again later."
        ) from exc
