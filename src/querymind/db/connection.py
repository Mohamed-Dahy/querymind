"""The one place the agent opens database connections.

Every agent query goes through `connect_readonly`, so it always runs as the read-only role
(analyst_ro). The admin connection is for setup scripts only and never used here.
"""

import psycopg

from querymind.config import Settings


def connect_readonly(settings: Settings) -> psycopg.Connection:
    """Open a new connection as the read-only role. The caller must close it (use `with`)."""
    return psycopg.connect(settings.readonly_conninfo())
