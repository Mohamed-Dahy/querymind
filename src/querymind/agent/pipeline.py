"""The Phase 1 pipeline: question → schema → SQL → validate → execute → answer.

    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    result = pipeline.ask("Which 5 products are the most expensive?")
    result.answer.text, result.sql, result.result.rows

One object per app (the API keeps one): it reads the schema once and reuses it, and opens a
fresh read-only connection per question. Phase 2 replaces this straight line with a LangGraph
agent (retries, clarifying questions), reusing the same building blocks.

Every question writes one structured "ask" log line with the outcome (CLAUDE.md §8.7; the real
audit table arrives in Phase 5).
"""

import logging
import threading
import time
from collections.abc import Callable

import psycopg

from querymind.agent.schemas import AskResult
from querymind.agent.sql_generator import generate_sql
from querymind.agent.summarizer import summarize
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.executor import execute_query
from querymind.db.schema import SchemaInfo, describe_schema, introspect_schema
from querymind.llm import LLMProvider
from querymind.safety.sql_validator import validate_sql

audit_logger = logging.getLogger("querymind.audit")


class Pipeline:
    """Answers questions about the database, one at a time, safely."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        settings: Settings,
        connect: Callable[[Settings], psycopg.Connection] = connect_readonly,
    ) -> None:
        """`connect` must open a *read-only* connection; it's a parameter so tests can swap it."""
        self._llm = llm
        self._settings = settings
        self._connect = connect
        self._schema: tuple[SchemaInfo, str] | None = None
        self._schema_lock = threading.Lock()  # the API may call ask() from several threads

    def ask(self, question: str) -> AskResult:
        """Answer one question.

        Raises:
            ValueError: the question is empty.
            LLMProviderError / LLMOutputError: the LLM could not write the SQL.
            UnsafeSqlError: the SQL failed validation.
            QueryTimeoutError / QueryExecutionError: the database could not run it.
        """
        started = time.perf_counter()
        sql: str | None = None
        try:
            with self._connect(self._settings) as conn:
                schema, schema_text = self._schema_for(conn)
                draft = generate_sql(self._llm, schema_text=schema_text, question=question)
                sql = draft.sql
                query = validate_sql(
                    draft.sql, allowed_tables=schema.table_names, max_rows=self._settings.max_rows
                )
                sql = query.sql
                result = execute_query(
                    conn, query, timeout_seconds=self._settings.query_timeout_seconds
                )
            # Outside the `with`: the connection is closed before the slower summary LLM call.
            answer = summarize(self._llm, question=question, result=result)
        except Exception as exc:
            self._audit(question, sql, outcome=type(exc).__name__, started=started)
            raise
        self._audit(question, sql, outcome="ok", started=started, row_count=result.row_count)
        return AskResult(
            question=question,
            sql=query.sql,
            explanation=draft.explanation,
            result=result,
            answer=answer,
        )

    def _schema_for(self, conn: psycopg.Connection) -> tuple[SchemaInfo, str]:
        """The schema and its prompt text, read once and then reused.

        Restart the app after changing the database structure to pick up new tables.
        """
        with self._schema_lock:
            if self._schema is None:
                schema = introspect_schema(conn)
                self._schema = (schema, describe_schema(schema))
            return self._schema

    def _audit(
        self,
        question: str,
        sql: str | None,
        *,
        outcome: str,
        started: float,
        row_count: int | None = None,
    ) -> None:
        audit_logger.info(
            "ask",
            extra={
                "question": question,
                "sql": sql,
                "outcome": outcome,
                "row_count": row_count,
                "provider": self._llm.name,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )
