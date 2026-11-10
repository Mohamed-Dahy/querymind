"""Errors raised when running a query. Callers catch these, never raw psycopg errors."""


class QueryError(Exception):
    """A validated query could not be run. The message is safe to show the user."""


class DatabaseUnavailableError(QueryError):
    """No connection could be opened (database down, wrong host/port or password)."""


class QueryTimeoutError(QueryError):
    """The query ran longer than the statement timeout and Postgres cancelled it."""


class QueryExecutionError(QueryError):
    """Postgres refused or failed the query (bad column name, type error, lost connection...).

    `detail` is Postgres's own short message (e.g. 'column "x" does not exist'): useful for the
    LLM to fix its SQL (Phase 2 self-correction).
    """

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


# --- Users' own databases (5.9). Every message is safe to show: it says what to fix, never
# more than the user already knows (no internal addresses, no server replies). ---


class RemoteDatabaseError(Exception):
    """A user's own database can't be used. The message is safe to show."""


class UnsafeConnectionError(RemoteDatabaseError):
    """Refused before connecting: a private or internal address, a non-database port, a bad
    name, or an unencrypted connection outside development."""


class RemoteUnreachableError(RemoteDatabaseError):
    """No connection: unknown host, closed port, timeout, network down. One message for all, so
    the check can't be used to map which ports are open where."""


class RemoteLoginError(RemoteDatabaseError):
    """The server answered and refused the login (user, password, or database name)."""


class RemoteTlsError(RemoteDatabaseError):
    """The encrypted connection failed: no TLS on the server, or a certificate that can't be
    verified (with verify-full)."""


class WritableLoginError(RemoteDatabaseError):
    """The login can change the database, so QueryMind won't use it. `problems` lists why."""

    def __init__(self, message: str, *, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems
