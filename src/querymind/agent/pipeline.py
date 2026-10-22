"""The front door of the agent: question → AskResult. Everything else calls this.

    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    result = pipeline.ask("Which 5 products are the most expensive?")
    result.answer.text, result.sql, result.result.rows

One object per app (the API keeps one): it reads the schema once and reuses it, builds the
agent graph once (agent/graph.py), and runs the graph for each question. The API, the eval
runner and scripts/ask.py only see `ask()`, so the graph can grow without changing them.

Every question writes one structured "ask" log line with the outcome (CLAUDE.md §8.7; the real
audit table arrives in Phase 5).
"""

import logging
import threading
import time
from collections.abc import Callable
from typing import cast

import psycopg

from querymind.agent.graph import build_graph
from querymind.agent.schemas import AskResult
from querymind.agent.state import AgentContext, AgentState
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.schema import SchemaInfo, describe_schema, introspect_schema
from querymind.llm import LLMProvider

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
            UnsafeSqlError: the SQL failed validation.
            DatabaseUnavailableError / QueryTimeoutError / QueryExecutionError: the database
                could not run it.
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
        except Exception as exc:
            self._audit(question, state.get("sql"), outcome=type(exc).__name__, started=started)
            raise
        result = state["result"]
        self._audit(
            question, state["sql"], outcome="ok", started=started, row_count=result.row_count
        )
        return AskResult(
            question=question,
            sql=state["sql"],
            explanation=state["explanation"],
            result=result,
            answer=state["answer"],
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
