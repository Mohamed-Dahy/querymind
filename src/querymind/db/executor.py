"""Run a validated query as the read-only role, with a timeout and a row cap.

    with connect_readonly(settings) as conn:
        result = execute_query(conn, validated, timeout_seconds=10)
    result.columns, result.rows, result.truncated

Safety layers applied here, on top of the validator (CLAUDE.md §8.1 and §8.3):
  - only a `ValidatedQuery` is accepted, never a plain string;
  - the transaction is READ ONLY, even if the role's default were ever changed;
  - `SET LOCAL statement_timeout` stops long queries (and is reset when the transaction ends);
  - a server-side cursor sends at most `max_rows + 1` rows over the network, whatever the SQL;
  - psycopg errors become our own QueryError types with short, user-safe messages.
"""

import datetime as dt
import logging
import time
import uuid
from decimal import Decimal

import psycopg
from psycopg import errors, sql
from pydantic import BaseModel, ConfigDict, JsonValue

from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.safety.sql_validator import ValidatedQuery

logger = logging.getLogger(__name__)


class QueryResult(BaseModel):
    """What a query returned, ready to send as JSON (API, UI, LLM summary)."""

    model_config = ConfigDict(frozen=True)

    columns: list[str]
    rows: list[list[JsonValue]]
    row_count: int  # rows returned here (at most max_rows)
    truncated: bool  # True if more rows existed and were cut off
    duration_ms: int


def execute_query(
    conn: psycopg.Connection, query: ValidatedQuery, *, timeout_seconds: int
) -> QueryResult:
    """Run `query` on `conn` (a read-only connection) and return its rows as JSON-safe values.

    Raises:
        TypeError: `query` is not a ValidatedQuery (e.g. a plain SQL string).
        QueryTimeoutError: it ran longer than `timeout_seconds`.
        QueryExecutionError: Postgres reported any other error.
    """
    # mypy already rejects a plain string; this also catches it at runtime (e.g. untyped code).
    if not isinstance(query, ValidatedQuery):
        raise TypeError(f"execute_query needs a ValidatedQuery, got {type(query).__name__}")

    started = time.perf_counter()
    try:
        # One transaction per query: committed if it works, rolled back on any error, so the
        # connection is clean for the next query either way.
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(
                sql.SQL("SET LOCAL statement_timeout = {}").format(
                    sql.Literal(f"{timeout_seconds}s")
                )
            )
            # A *named* cursor lives on the server (DECLARE ... CURSOR): rows only cross the
            # network when we fetch them, so we never receive more than max_rows + 1.
            with conn.cursor(name="querymind_result") as cursor:
                cursor.execute(query.sql)
                raw_rows = cursor.fetchmany(query.max_rows + 1)
                columns = [column.name for column in cursor.description or []]
    except errors.QueryCanceled as exc:
        logger.warning("query timed out", extra={"sql": query.sql, "timeout_s": timeout_seconds})
        raise QueryTimeoutError(
            f"The query took longer than {timeout_seconds} s and was stopped."
        ) from exc
    except psycopg.Error as exc:
        detail = _postgres_message(exc)
        logger.warning("query failed", extra={"sql": query.sql, "detail": detail})
        raise QueryExecutionError(
            f"The database could not run the query: {detail}", detail=detail
        ) from exc

    truncated = len(raw_rows) > query.max_rows
    rows = [[to_json_value(value) for value in row] for row in raw_rows[: query.max_rows]]
    duration_ms = round((time.perf_counter() - started) * 1000)
    logger.info(
        "query executed",
        extra={
            "tables": sorted(query.tables),
            "row_count": len(rows),
            "truncated": truncated,
            "duration_ms": duration_ms,
        },
    )
    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
        duration_ms=duration_ms,
    )


def to_json_value(value: object) -> JsonValue:
    """Convert one database value to something JSON can hold.

    Decimal → float, dates/times → ISO text, intervals → text like '3 days, 0:00:00',
    UUID → text, arrays → lists, raw bytes → a size note (never the bytes themselves).
    """
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.time):  # dt.datetime is a dt.date too
        return value.isoformat()
    if isinstance(value, dt.timedelta | uuid.UUID):
        return str(value)
    if isinstance(value, bytes | memoryview):
        return f"<binary, {len(value)} bytes>"
    if isinstance(value, list | tuple):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict):  # json / jsonb columns
        return {str(key): to_json_value(item) for key, item in value.items()}
    return str(value)


def _postgres_message(exc: psycopg.Error) -> str:
    """Postgres's short primary message, without connection details or internal context."""
    primary = exc.diag.message_primary if exc.diag else None
    return primary or type(exc).__name__
