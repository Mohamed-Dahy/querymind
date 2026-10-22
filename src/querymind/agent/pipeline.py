"""The front door of the agent: question → AskResult. Everything else calls this.

    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    result = pipeline.ask("Which 5 products are the most expensive?")
    result.answer.text, result.sql, result.result.rows

One object per app (the API keeps one): it reads the schema once and reuses it, builds the
agent graph once (agent/graph.py), and runs the graph for each question. The API, the eval
runner and scripts/ask.py only see `ask()`, so the graph can grow without changing them.

Every question writes one structured "ask" log line with the outcome and every SQL attempt
(CLAUDE.md §8.7; the real audit table arrives in Phase 5).
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import cast

import psycopg

from querymind.agent.graph import build_graph
from querymind.agent.schemas import AskResult, SqlAttempt
from querymind.agent.state import AgentContext, AgentState
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.db.schema import SchemaInfo, describe_schema, introspect_schema
from querymind.llm import LLMProvider
from querymind.safety.errors import UnsafeSqlError

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
        self._graph = build_graph()
        self._schema: tuple[SchemaInfo, str] | None = None
        self._schema_lock = threading.Lock()  # the API may call ask() from several threads

    def ask(self, question: str, *, summarize_answer: bool = True) -> AskResult:
        """Answer one question.

        `summarize_answer=False` skips the second LLM call and uses the plain templated answer.
        The eval uses it: it scores the SQL's results, so the summary would only cost tokens.

        Raises:
            ValueError: the question is empty.
            LLMProviderError / LLMOutputError: the LLM could not write the SQL.
            UnsafeSqlError / QueryExecutionError / QueryTimeoutError: every SQL attempt failed
                (up to `settings.max_sql_attempts`); the error is the last attempt's.
            DatabaseUnavailableError: the database could not be reached.
        """
        started = time.perf_counter()
        state: AgentState = {"question": question}
        try:
            schema, schema_text = self._schema_info()
            start: AgentState = {
                "question": question,
                "schema_text": schema_text,
                "table_names": sorted(schema.table_names),
            }
            context = AgentContext(
                llm=self._llm,
                settings=self._settings,
                connect=self._connect,
                summarize_answer=summarize_answer,
            )
            # stream(mode="values") yields the whole state after each node. Keeping the last
            # one means that if a node fails, we still know the SQL it was given (for the audit).
            for snapshot in self._graph.stream(start, context=context, stream_mode="values"):
                state = cast(AgentState, snapshot)  # LangGraph types this loosely as dict
            if "result" not in state:  # the graph gave up: every SQL attempt failed
                raise _error_from(state["attempts"][-1])
        except Exception as exc:
            self._audit(question, state, outcome=type(exc).__name__, started=started)
            raise
        self._audit(question, state, outcome="ok", started=started)
        return AskResult(
            question=question,
            sql=state["sql"],
            explanation=state["explanation"],
            result=state["result"],
            answer=state["answer"],
            failed_attempts=tuple(state.get("attempts", [])),
            sql_attempts=_queries_run(state),
            warnings=tuple(state.get("warnings", [])),
        )

    def _schema_info(self) -> tuple[SchemaInfo, str]:
        """The schema and its prompt text, read once (on its own connection) and then reused.

        Restart the app after changing the database structure to pick up new tables.
        """
        with self._schema_lock:
            if self._schema is None:
                with self._connect(self._settings) as conn:
                    schema = introspect_schema(conn)
                self._schema = (schema, describe_schema(schema))
            return self._schema

    def _audit(self, question: str, state: AgentState, *, outcome: str, started: float) -> None:
        failed = state.get("attempts", [])
        result = state.get("result")
        audit_logger.info(
            "ask",
            extra={
                "question": question,
                "sql": _last_sql(state),  # validated, if it got that far
                "outcome": outcome,
                "row_count": result.row_count if result else None,
                # Queries tried, and why each failed (or suspicious) one was retried.
                "sql_attempts": _queries_run(state),
                "attempt_errors": [f"{a.error_kind}: {a.detail}" for a in failed],
                "warnings": [w.code for w in state.get("warnings", [])],
                "provider": self._llm.name,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )


def _queries_run(state: AgentState) -> int:
    """SQL queries tried. Each attempt was one; a result that isn't an attempt is one more.

    The exception: after a suspicious result whose repairs all failed, the answer *is* that
    suspicious attempt's result (still flagged for repair), so it isn't counted twice.
    """
    attempts = len(state.get("attempts", []))
    answered_with_new_query = "result" in state and not state.get("needs_repair", False)
    return attempts + (1 if answered_with_new_query else 0)


def _last_sql(state: AgentState) -> str | None:
    """The SQL to report: the one whose result we have, else the last one tried, else the draft."""
    if "result" in state:
        return state["sql"]
    if attempts := state.get("attempts"):
        return attempts[-1].sql
    draft = state.get("draft")
    return draft.sql if draft else None


def _error_from(attempt: SqlAttempt) -> Exception:
    """The exception for a failed attempt, so the API maps it exactly as before (422/502/504)."""
    if attempt.error_kind == "unsafe":
        return UnsafeSqlError(attempt.message)
    if attempt.error_kind == "db_error":
        return QueryExecutionError(attempt.message, detail=attempt.detail)
    if attempt.error_kind == "timeout":
        return QueryTimeoutError(attempt.message)
    # A suspicious attempt always leaves a result to answer with, so it never ends a run.
    raise AssertionError(f"no error for a {attempt.error_kind!r} attempt")
