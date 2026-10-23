"""The front door of the agent: question → AskResult. Everything else calls this.

    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    result = pipeline.ask("Which 5 products are the most expensive?")
    result.answer.text, result.sql, result.result.rows

One object per app (the API keeps one): it reads the schema once and reuses it, builds the
agent graph once (agent/graph.py), and runs the graph for each question. The API, the eval
runner and scripts/ask.py only see `ask()`, so the graph can grow without changing them.

Conversations (2.6): `ask(question, conversation_id="...")` continues that conversation, so a
follow-up like "now only for 1997" works. LangGraph's in-memory checkpointer keeps each
conversation's history (its last turns, no rows) between turns. Without an id, each question
stands alone (evals, scripts).
Limits: memory is lost on restart, only the last MAX_CONVERSATIONS are kept, and anyone with
an id can continue its conversation (Phase 5 ties conversations to users).

Schema retrieval (3.3): on a big schema the LLM sees only the tables a question needs. It's
decided once, at the first question: SCHEMA_RETRIEVAL, the number of tables, an embedding
provider, and a usable index (scripts/build_index.py). In `auto`, anything missing means the
whole schema plus a warning; in `on`, it's an error.

Every question writes one structured "ask" log line with the outcome, every SQL attempt and
which tables the LLM was shown (CLAUDE.md §8.7; the real audit table arrives in Phase 5).
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable
from typing import cast

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from querymind.agent.confidence import score_confidence
from querymind.agent.graph import build_graph
from querymind.agent.schemas import (
    AgentReply,
    AskResult,
    ClarificationRequest,
    Refusal,
    SqlAttempt,
)
from querymind.agent.state import CHECKPOINT_TYPES, AgentContext, AgentState, new_turn, require
from querymind.config import ConfigError, Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.db.schema import introspect_schema
from querymind.llm import LLMProvider
from querymind.llm.embeddings import EmbeddingProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.semantic.documents import index_documents
from querymind.semantic.examples import load_all_examples
from querymind.semantic.index import INDEX_TABLE, SchemaIndexError, index_status
from querymind.semantic.loader import check_against, load_semantic_layers
from querymind.semantic.models import SemanticLayer
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("querymind.audit")

MAX_CONVERSATIONS = 100  # saved conversations; the least recently used is forgotten first


class Pipeline:
    """Answers questions about the database, one at a time, safely."""

    def __init__(
        self,
        *,
        llm: LLMProvider,
        settings: Settings,
        connect: Callable[[Settings], psycopg.Connection] = connect_readonly,
        embeddings: EmbeddingProvider | None = None,
        index_table: str = INDEX_TABLE,
    ) -> None:
        """`connect` must open a *read-only* connection; it's a parameter so tests can swap it.

        `embeddings` (from `create_embedding_provider`) enables schema retrieval; None = the
        LLM always sees the whole schema. `index_table` lets tests use their own index.

        Raises:
            ConfigError: SCHEMA_RETRIEVAL=on without an embedding provider (GOOGLE_API_KEY).
        """
        if settings.schema_retrieval == "on" and embeddings is None:
            raise ConfigError("SCHEMA_RETRIEVAL=on needs embeddings: set GOOGLE_API_KEY")
        self._llm = llm
        self._settings = settings
        self._connect = connect
        self._embeddings = embeddings
        self._index_table = index_table
        # The serializer may only rebuild our own state classes from a saved checkpoint.
        serde = JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)
        self._checkpointer = InMemorySaver(serde=serde)
        self._graph = build_graph(checkpointer=self._checkpointer)
        # Read the business definitions now: a broken file fails at startup, not on a question.
        self._semantic_layers: list[SemanticLayer] = load_semantic_layers(
            settings.semantic_layer_paths
        )
        self._examples = load_all_examples(settings.semantic_layer_paths)  # few-shot (3.5)
        self._schema: tuple[SchemaCatalog, SchemaRetriever | None] | None = None
        self._schema_lock = threading.Lock()  # the API may call ask() from several threads
        self._conversations: OrderedDict[str, None] = OrderedDict()  # least recent first
        self._conversations_lock = threading.Lock()

    def ask(
        self, question: str, *, conversation_id: str | None = None, summarize_answer: bool = True
    ) -> AgentReply:
        """Answer one question, or ask back when it's ambiguous, or refuse a change request.

        `conversation_id` continues an earlier conversation (the LLM sees its last turns); a new
        id starts one. None: the question stands alone and nothing is kept.

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
        # A standalone question still needs a thread for the checkpointer: a throwaway one.
        thread_id = conversation_id or f"standalone-{uuid.uuid4()}"
        try:
            catalog, retriever = self._schema_info()
            start = new_turn(
                question=question,
                schema_text=catalog.full_text,  # retrieve_schema narrows it when it can
                table_names=sorted(catalog.schema.table_names),
            )
            context = AgentContext(
                llm=self._llm,
                settings=self._settings,
                connect=self._connect,
                summarize_answer=summarize_answer,
                retriever=retriever,
            )
            # stream(mode="values") yields the whole state after each node. Keeping the last
            # one means that if a node fails, we still know the SQL it was given (for the audit).
            # durability="exit": save the state once, when the turn ends, not after every node.
            for snapshot in self._graph.stream(
                start,
                {"configurable": {"thread_id": thread_id}},
                context=context,
                stream_mode="values",
                durability="exit",
            ):
                state = cast(AgentState, snapshot)  # LangGraph types this loosely as dict
            reply = _reply_without_result(question, state)
        except Exception as exc:
            self._audit(
                question,
                state,
                outcome=type(exc).__name__,
                started=started,
                conversation_id=conversation_id,
            )
            raise
        finally:
            self._keep_or_forget(thread_id, keep=conversation_id is not None)
        if reply is not None:
            outcome = "refusal" if isinstance(reply, Refusal) else "clarification"
            self._audit(
                question, state, outcome=outcome, started=started, conversation_id=conversation_id
            )
            return reply
        result = require(state.get("result"), "result")
        confidence = score_confidence(
            failed_attempts=state.get("attempts", []),
            warnings=state.get("warnings", []),
            truncated=result.truncated,
            self_assessment=state.get("self_assessment"),
        )
        self._audit(
            question,
            state,
            outcome="ok",
            started=started,
            conversation_id=conversation_id,
            confidence=confidence.level,
        )
        return AskResult(
            question=question,
            sql=require(state.get("sql"), "sql"),
            explanation=require(state.get("explanation"), "explanation"),
            result=result,
            answer=require(state.get("answer"), "answer"),
            failed_attempts=tuple(state.get("attempts", [])),
            sql_attempts=_queries_run(state),
            warnings=tuple(state.get("warnings", [])),
            confidence=confidence,
        )

    def _schema_info(self) -> tuple[SchemaCatalog, SchemaRetriever | None]:
        """The schema with its prompt texts, and the retriever if retrieval is on. Read once
        (on its own connection) and then reused.

        Restart the app after changing the database structure to pick up new tables.
        """
        with self._schema_lock:
            if self._schema is None:
                with self._connect(self._settings) as conn:
                    schema = introspect_schema(conn)
                for layer in self._semantic_layers:
                    check_against(layer, schema)  # every name it uses must exist (3.1)
                catalog = SchemaCatalog(schema, self._semantic_layers, self._examples)
                self._schema = (catalog, self._make_retriever(catalog))
            return self._schema

    def _make_retriever(self, catalog: SchemaCatalog) -> SchemaRetriever | None:
        """A retriever when retrieval should and can run; None = send the whole schema.

        Raises:
            SchemaIndexError: SCHEMA_RETRIEVAL=on, but the index is missing or unusable.
        """
        settings = self._settings
        mode = settings.schema_retrieval
        tables = len(catalog.schema.tables)
        if mode == "off" or (mode == "auto" and tables <= settings.retrieval_min_tables):
            return None
        if self._embeddings is None:  # only in auto: `on` was refused in __init__
            logger.warning(
                "schema retrieval skipped: no embeddings (set GOOGLE_API_KEY)",
                extra={"tables": tables},
            )
            return None
        docs = index_documents(catalog.schema, catalog.layers, settings.semantic_layer_paths)
        with self._connect(settings) as conn:
            status = index_status(conn, docs, model=self._embeddings.name, table=self._index_table)
        if not status.usable:
            if mode == "on":
                raise SchemaIndexError(f"Schema retrieval can't run: {status.message}")
            logger.warning("schema retrieval skipped", extra={"reason": status.message})
            return None
        if status.message:  # usable but out of date: still better than the whole schema
            logger.warning("schema index out of date", extra={"reason": status.message})
        return SchemaRetriever(
            catalog=catalog,
            embeddings=self._embeddings,
            connect=self._connect,
            settings=settings,
            index_table=self._index_table,
            llm=self._llm,  # only used by RETRIEVAL_MODE=hybrid_rerank
            documents={d.table: d.text for d in docs if d.kind == "table"},
        )

    def _keep_or_forget(self, thread_id: str, *, keep: bool) -> None:
        """After a turn: save only what the next turn needs, for the last MAX_CONVERSATIONS
        conversations. A standalone question's thread is deleted at once.

        The checkpoint LangGraph saved holds the whole state, rows included, and every turn
        would add one. So it is replaced by a single checkpoint with just the history: memory
        stays small, and no rows are held between turns (§8.5).
        """
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        # Read from the checkpointer, not from the streamed state: a turn that failed early
        # has no history in its state, and that must not wipe the conversation.
        history = self._graph.get_state(config).values.get("history", []) if keep else []
        # Delete first: writing to the live thread would run the reducer (old + old history).
        self._checkpointer.delete_thread(thread_id)
        if not keep:
            return
        if history:
            self._graph.update_state(config, {"history": history}, as_node="remember")
        with self._conversations_lock:
            self._conversations[thread_id] = None
            self._conversations.move_to_end(thread_id)
            while len(self._conversations) > MAX_CONVERSATIONS:
                oldest, _ = self._conversations.popitem(last=False)
                self._checkpointer.delete_thread(oldest)

    def _audit(
        self,
        question: str,
        state: AgentState,
        *,
        outcome: str,
        started: float,
        conversation_id: str | None,
        confidence: str | None = None,
    ) -> None:
        failed = state.get("attempts", [])
        result = state.get("result")
        shown = state.get("schema_selection")
        audit_logger.info(
            "ask",
            extra={
                "question": question,
                "conversation_id": conversation_id,
                "sql": _last_sql(state),  # validated, if it got that far
                "outcome": outcome,
                "row_count": result.row_count if result else None,
                # Queries tried, and why each failed (or suspicious) one was retried.
                "sql_attempts": _queries_run(state),
                "attempt_errors": [f"{a.error_kind}: {a.detail}" for a in failed],
                "warnings": [w.code for w in state.get("warnings", [])],
                # What the LLM was shown (3.3): "full", "retrieved", or "fallback"; the search
                # that ran (3.4); and why it fell back or was weaker (e.g. keyword only).
                "schema_mode": shown.mode if shown else None,
                "schema_tables": list(shown.tables) if shown else None,
                "schema_tokens": shown.schema_tokens if shown else None,
                "schema_search": (shown.search or None) if shown else None,
                "schema_fallback": (shown.reason or None) if shown else None,
                "examples": list(shown.examples) if shown else [],  # few-shot ids (3.5)
                "search_question": state.get("search_question"),  # the rewritten follow-up (3.6)
                "confidence": confidence,
                "provider": self._llm.name,
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )


def _reply_without_result(
    question: str, state: AgentState
) -> Refusal | ClarificationRequest | None:
    """When the run ended without a result: a refusal, a question back, or (raised) the error.

    None means there is a result, so the caller builds the answer.
    """
    if state.get("result") is not None:
        return None
    draft = state.get("draft")
    if draft is not None and draft.refusal is not None:
        return Refusal(question=question, message=draft.refusal)
    if draft is not None and draft.clarification is not None:
        return ClarificationRequest(question=question, clarification=draft.clarification)
    raise _error_from(state["attempts"][-1])  # the graph gave up: every SQL attempt failed


def _queries_run(state: AgentState) -> int:
    """SQL queries tried. Each attempt was one; a result that isn't an attempt is one more.

    The exception: after a suspicious result whose repairs all failed, the answer *is* that
    suspicious attempt's result (still flagged for repair), so it isn't counted twice.
    """
    attempts = len(state.get("attempts", []))
    has_result = state.get("result") is not None
    answered_with_new_query = has_result and not state.get("needs_repair", False)
    return attempts + (1 if answered_with_new_query else 0)


def _last_sql(state: AgentState) -> str | None:
    """The SQL to report: the one whose result we have, else the last one tried, else the draft."""
    if state.get("result") is not None:
        return state.get("sql")
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
