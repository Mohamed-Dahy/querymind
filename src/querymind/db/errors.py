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
