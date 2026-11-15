"""The front door of the agent: question → AskResult. Everything else calls this.

    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    result = pipeline.ask("Which 5 products are the most expensive?")
    result.answer.text, result.sql, result.result.rows

One object per app (the API keeps one): it reads the schema once and reuses it, builds the
agent graph once (agent/graph.py), and runs the graph for each question. The API, the eval
runner and scripts/ask.py only see `ask()`, so the graph can grow without changing them.
`stream()` is the same run, reporting each step as it happens (UI U2.1); `ask()` consumes it,
so both share the audit log, the conversation memory and the error handling.

Conversations (2.6): `ask(question, conversation_id="...")` continues that conversation, so a
follow-up like "now only for 1997" works. LangGraph's in-memory checkpointer keeps each
conversation's history (its last turns, no rows) between turns. Without an id, each question
stands alone (evals, scripts).
Only the last MAX_CONVERSATIONS are kept, and a restart loses them all. So with a
`history_loader` (the saved conversations, U3.2), one not in memory is rebuilt when next asked.
Limit: anyone with an id can continue its conversation (Phase 5 ties conversations to users).

Schema retrieval (3.3): on a big schema the LLM sees only the tables a question needs. It's
decided once, at the first question: SCHEMA_RETRIEVAL, the number of tables, an embedding
provider, and a usable index (scripts/build_index.py). In `auto`, anything missing means the
whole schema plus a warning; in `on`, it's an error. A saved database (5.10b) has its own
index in the app database: synced at the first question (no LLM call), searched by keyword.

Table permissions (5.2): one pipeline serves every user of its database, so the tables a user may
use come with each call (`allowed_tables`, None = all). The run then sees a restricted view of the
catalog (safety/permissions.py): the prompt, retrieval, examples and the validator's allowlist only
know those tables. Views are cached per set of visible tables.

PII masking (5.4): `mask_pii=True` (every role but admin) masks personal data. The view then also
hides personal sample values and names the masked columns in the prompt, and the run checks each
query with a PiiGuard (safety/pii.py): personal columns may only be selected as they are, counted
or grouped by, and are masked in the result before the summary or the reply sees it. The personal
columns: the YAML layers' flags (the demo), or a saved database's dictionary flags and name
guesses (`personal_columns`).

Row-level security (5.3): `row_scope` (the user's tags, None = every row) goes to the executor with
every query, and the database's policies filter the rows (safety/row_scope.py). A scoped view has
no sample rows from tables with row security: they were read once, with every row.

Every question writes one structured "ask" log line with the outcome, every SQL attempt and
which tables the LLM was shown (CLAUDE.md §8.7; the real audit table arrives in Phase 5).
"""

import logging
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, closing, nullcontext
from typing import Any, cast

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from querymind.agent.confidence import score_confidence
from querymind.agent.events import AgentEvent, Finished, TraceBuilder, shown_schema
from querymind.agent.graph import FIXABLE_ERRORS, build_graph, failed_attempt
from querymind.agent.schemas import (
    AgentReply,
    AnswerSource,
    AskResult,
    ClarificationRequest,
    Refusal,
    SqlAttempt,
    Turn,
)
from querymind.agent.state import CHECKPOINT_TYPES, AgentContext, AgentState, new_turn, require
from querymind.config import ConfigError, Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import read_schema
from querymind.llm import LLMProvider
from querymind.llm.embeddings import EmbeddingProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.permissions import NoPermittedTablesError
from querymind.safety.pii import NO_MASK, PiiGuard
from querymind.safety.row_scope import RowScope
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.dictionary import DataDictionary, layer_from_dictionary, personal_columns
from querymind.semantic.documents import build_documents, index_documents
from querymind.semantic.examples import load_all_examples
from querymind.semantic.index import (
    INDEX_TABLE,
    SavedDatabaseIndex,
    SchemaIndexError,
    index_status,
    sync_connection_documents,
)
from querymind.semantic.loader import check_against, load_semantic_layers
from querymind.semantic.models import SemanticLayer, answer_sources
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever

logger = logging.getLogger(__name__)
audit_logger = logging.getLogger("querymind.audit")

MAX_CONVERSATIONS = 100  # saved conversations; the least recently used is forgotten first
RUN_LOCKS = 64  # see Pipeline._one_run_at_a_time
MAX_VIEWS = 16  # restricted, masked or scoped catalogs kept: one per (tables, masked, scoped)

# A user's view of the database: the catalog, its retriever (None: the whole text), and the guard
# of a user whose personal data is masked (5.4; None: not masked).
type View = tuple[SchemaCatalog, SchemaRetriever | None, PiiGuard | None]


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
        history_loader: Callable[[str], list[Turn]] | None = None,
        source: AnswerSource | None = None,
        dictionary: DataDictionary | None = None,
        saved_index: SavedDatabaseIndex | None = None,
    ) -> None:
        """`connect` must open a *read-only* connection; it's a parameter so tests can swap it.

        `embeddings` (from `create_embedding_provider`) enables schema retrieval; None = the
        LLM always sees the whole schema. `index_table` lets tests use their own index.
        `history_loader` gives a conversation's earlier turns when they aren't in memory (after a
        restart). None = nothing outlives this object: what evals and scripts want.
        `source` names the database in every answer when no semantic layer does (5.11: a
        workspace's own database, which has no YAML layers). `dictionary` (5.10: drafted at
        onboarding) becomes that database's semantic layer, built against the schema read at the
        first question, so a dropped table or column is simply left out. `saved_index` (5.10b): that
        database's schema index lives in the app database and is searched by keyword only.

        Raises:
            ConfigError: SCHEMA_RETRIEVAL=on without an embedding provider (GOOGLE_API_KEY), or a
                saved index with a RETRIEVAL_MODE other than keyword.
        """
        if saved_index is not None:
            if settings.retrieval_mode != "keyword":
                raise ConfigError("A saved database's schema index is searched by keyword only.")
        elif settings.schema_retrieval == "on" and embeddings is None:
            raise ConfigError("SCHEMA_RETRIEVAL=on needs embeddings: set GOOGLE_API_KEY")
        self._llm = llm
        self._settings = settings
        self._connect = connect
        self._embeddings = embeddings
        self._index_table = index_table
        self._history_loader = history_loader
        self._source = source
        self._dictionary = dictionary
        self._saved_index = saved_index
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
        self._personal: frozenset[tuple[str, str]] = frozenset()  # 5.4, set with the schema
        self._schema_lock = threading.Lock()  # the API may call ask() from several threads
        # (visible tables, masked, scoped) -> that view; least recently used first (5.2-5.4).
        self._views: OrderedDict[tuple[frozenset[str], bool, bool], View] = OrderedDict()
        self._views_lock = threading.Lock()
        self._conversations: OrderedDict[str, None] = OrderedDict()  # least recent first
        self._conversations_lock = threading.Lock()
        self._run_locks = [threading.Lock() for _ in range(RUN_LOCKS)]

    def ask(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        summarize_answer: bool = True,
        llm: LLMProvider | None = None,
        allowed_tables: frozenset[str] | None = None,
        mask_pii: bool = False,
        row_scope: RowScope = None,
    ) -> AgentReply:
        """Answer one question, or ask back when it's ambiguous, or refuse a change request.

        `conversation_id` continues an earlier conversation (the LLM sees its last turns); a new
        id starts one. None: the question stands alone and nothing is kept.

        `summarize_answer=False` skips the second LLM call and uses the plain templated answer.
        The eval uses it: it scores the SQL's results, so the summary would only cost tokens.

        `llm` answers this one question instead of the pipeline's own (5.8: the workspace's own
        key). Every LLM call of the run uses it; embeddings stay the pipeline's.

        `allowed_tables` (5.2): the only tables this user may use; None = every table. The LLM is
        shown only those, and the validator refuses SQL on any other.

        `mask_pii` (5.4): this user's personal data is masked (every role but admin). A query
        that filters, sorts or computes with a personal column is refused (and repaired).

        `row_scope` (5.3): the rows this user may see (None = every row), for the database's
        row-level security.

        Raises:
            ValueError: the question is empty.
            NoPermittedTablesError: `allowed_tables` leaves no table of this database.
            LLMProviderError / LLMOutputError: the LLM could not write the SQL.
            UnsafeSqlError / QueryExecutionError / QueryTimeoutError: every SQL attempt failed
                (up to `settings.max_sql_attempts`); the error is the last attempt's.
            DatabaseUnavailableError: the database could not be reached.
        """
        run = self.stream(
            question,
            conversation_id=conversation_id,
            summarize_answer=summarize_answer,
            llm=llm,
            allowed_tables=allowed_tables,
            mask_pii=mask_pii,
            row_scope=row_scope,
        )
        with closing(run) as events:
            for event in events:
                if isinstance(event, Finished):
                    return event.reply
        raise RuntimeError("the run ended without a reply")  # stream() always ends with one

    def stream(
        self,
        question: str,
        *,
        conversation_id: str | None = None,
        summarize_answer: bool = True,
        llm: LLMProvider | None = None,
        allowed_tables: frozenset[str] | None = None,
        mask_pii: bool = False,
        row_scope: RowScope = None,
    ) -> Generator[AgentEvent | Finished]:
        """The same run as `ask()`, reporting as it goes. The last item is `Finished(reply)`.

        Raises what `ask()` raises, after the events so far. Closing the generator stops the run
        after the node that is working now: a node can't be interrupted, so an LLM call already
        made still finishes. A stopped run is audited too, and leaves the conversation as it was.
        """
        started = time.perf_counter()
        llm = llm or self._llm
        state: AgentState = {"question": question}
        # A standalone question still needs a thread for the checkpointer: a throwaway one.
        thread_id = conversation_id or f"standalone-{uuid.uuid4()}"
        with self._one_run_at_a_time(conversation_id):
            try:
                if conversation_id is not None:
                    self._restore_history(conversation_id)
                # 5.2: only what this user may see; 5.4: personal data masked if it must be.
                # 5.3: a scoped user's view has no sample rows from row-secured tables.
                catalog, retriever, guard = self._view(
                    allowed_tables, mask_pii, scoped=row_scope is not None
                )
                start = new_turn(
                    question=question,
                    schema_text=catalog.full_text,  # retrieve_schema narrows it when it can
                    table_names=sorted(catalog.schema.table_names),
                )
                context = AgentContext(
                    llm=llm,
                    settings=self._settings,
                    connect=self._connect,
                    summarize_answer=summarize_answer,
                    retriever=retriever,
                    pii_guard=guard,
                    row_scope=row_scope,
                )
                trace = TraceBuilder(total_tables=len(catalog.schema.table_names))
                # Three views of the same run. "values": the whole state after each node; keeping
                # the last one means that if a node fails, we still know the SQL it was given (for
                # the audit). "tasks": a node's start and end. "custom": the validator's signal.
                # durability="exit": save the state once, when the turn ends, not after every node.
                chunks = cast(
                    Generator[tuple[str, Any]],  # with several modes: (mode, chunk)
                    self._graph.stream(
                        start,
                        {"configurable": {"thread_id": thread_id}},
                        context=context,
                        stream_mode=["values", "tasks", "custom"],
                        durability="exit",
                    ),
                )
                # closing: a stop must end LangGraph's generator now, not when it is collected.
                with closing(chunks):
                    for mode, chunk in chunks:
                        if mode == "values":
                            state = cast(AgentState, chunk)  # LangGraph types it loosely as dict
                        else:
                            yield from trace.events(mode, chunk)
                reply = _reply_without_result(question, state)
            except GeneratorExit:
                # Not an Exception, so the branch below wouldn't see it. The caller stopped the
                # run (the browser went away), and every question must still be audited (§8.7).
                self._audit(
                    question,
                    state,
                    outcome="cancelled",
                    started=started,
                    conversation_id=conversation_id,
                    provider=llm.name,
                )
                raise
            except Exception as exc:
                self._audit(
                    question,
                    state,
                    outcome=type(exc).__name__,
                    started=started,
                    conversation_id=conversation_id,
                    provider=llm.name,
                )
                raise
            finally:
                self._keep_or_forget(thread_id, keep=conversation_id is not None)
        yield Finished(
            self._finish(question, state, reply, started, conversation_id, provider=llm.name)
        )

    def rerun(
        self,
        question: str,
        sql: str,
        *,
        conversation_id: str | None = None,
        allowed_tables: frozenset[str] | None = None,
        mask_pii: bool = False,
        row_scope: RowScope = None,
    ) -> QueryResult:
        """Run a saved answer's SQL again for fresh rows, with no LLM call.

        The SQL comes from storage, so it isn't trusted: it goes through the validator again, with
        today's tables and row limit, and runs as the read-only role. Audited like any question.
        `allowed_tables` (5.2): as for `ask`, so a saved answer can't reach a table hidden since.
        `mask_pii` (5.4): as for `ask`: the fresh rows are masked, and saved SQL is checked like
        new SQL (it may date from before this user's personal data was masked).
        `row_scope` (5.3): as for `ask`: fresh rows are today's rows for this user's scope.

        Raises:
            UnsafeSqlError / QueryExecutionError / QueryTimeoutError / DatabaseUnavailableError:
                as for a question, but with no repair: it's the same SQL, so it would fail again.
        """
        started = time.perf_counter()
        state: AgentState = {"question": question}
        try:
            catalog, _, guard = self._view(allowed_tables, mask_pii, scoped=row_scope is not None)
            query = validate_sql(
                sql,
                allowed_tables=catalog.schema.table_names,
                max_rows=self._settings.max_rows,
            )
            mask = guard.plan(query) if guard is not None else NO_MASK
            with self._connect(self._settings) as conn:
                result = mask.apply(
                    execute_query(
                        conn,
                        query,
                        timeout_seconds=self._settings.query_timeout_seconds,
                        row_scope=row_scope,
                    )
                )
        except Exception as exc:
            if isinstance(exc, FIXABLE_ERRORS):
                state["attempts"] = [failed_attempt(sql, exc)]  # so the audit shows what it tried
            self._audit(
                question,
                state,
                outcome=type(exc).__name__,
                started=started,
                conversation_id=conversation_id,
            )
            raise
        state["sql"] = query.sql
        state["result"] = result
        self._audit(
            question, state, outcome="rerun", started=started, conversation_id=conversation_id
        )
        return result

    def forget(self, conversation_id: str) -> None:
        """Drop a conversation from memory, so a deleted conversation can't come back in a prompt.

        Waits for any run of it to end first. Unknown ids are fine: there's nothing to do.
        """
        with self._one_run_at_a_time(conversation_id):
            self._checkpointer.delete_thread(conversation_id)
            with self._conversations_lock:
                self._conversations.pop(conversation_id, None)

    def _restore_history(self, conversation_id: str) -> None:
        """Put a conversation's saved turns back into memory, if memory has none of them.

        Called inside the conversation's lock, so no run of it can be under way.
        """
        if self._history_loader is None:
            return
        config: RunnableConfig = {"configurable": {"thread_id": conversation_id}}
        if self._graph.get_state(config).values.get("history"):
            return
        turns = self._history_loader(conversation_id)
        if turns:
            self._graph.update_state(config, {"history": turns}, as_node="remember")

    def _one_run_at_a_time(self, conversation_id: str | None) -> AbstractContextManager[object]:
        """One run per conversation: the next one waits until the last has ended.

        Stop, then ask again at once, would otherwise put two runs on one thread, and the first
        run's clean-up (`_keep_or_forget`) would delete the second run's state. A fixed set of
        locks needs no clean-up; two conversations sharing one only wait for each other.
        """
        if conversation_id is None:
            return nullcontext()  # its thread is its own
        return self._run_locks[hash(conversation_id) % len(self._run_locks)]

    def _finish(
        self,
        question: str,
        state: AgentState,
        reply: AgentReply | None,
        started: float,
        conversation_id: str | None,
        *,
        provider: str,
    ) -> AgentReply:
        """The audit line of a run that ended normally, and its reply."""
        if reply is not None:
            outcome = "refusal" if isinstance(reply, Refusal) else "clarification"
            self._audit(
                question,
                state,
                outcome=outcome,
                started=started,
                conversation_id=conversation_id,
                provider=provider,
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
            provider=provider,
        )
        selection = state.get("schema_selection")
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
            schema_selection=(
                shown_schema(selection, total_tables=len(state.get("table_names", [])))
                if selection is not None
                else None
            ),
            tables_used=tuple(state.get("tables_used", [])),
            sources=(
                answer_sources(self._semantic_layers, state.get("tables_used", []))
                or ((self._source,) if self._source else ())
            ),
            search_question=state.get("search_question"),
            example_questions=tuple(e.question for e in state.get("examples", [])),
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    def _schema_info(self) -> tuple[SchemaCatalog, SchemaRetriever | None]:
        """The schema with its prompt texts, and the retriever if retrieval is on. Read once
        (on its own connection) and then reused.

        Restart the app after changing the database structure to pick up new tables.
        """
        with self._schema_lock:
            if self._schema is None:
                with self._connect(self._settings) as conn:
                    schema = read_schema(conn)
                if self._dictionary is not None and self._source is not None:
                    layer, skipped = layer_from_dictionary(
                        self._source.database, self._source.description, self._dictionary, schema
                    )
                    self._semantic_layers = [layer]
                    if skipped:
                        logger.info("names not described", extra={"names": skipped[:20]})
                for layer in self._semantic_layers:
                    check_against(layer, schema)  # every name it uses must exist (3.1)
                self._personal = (
                    personal_columns(self._dictionary, schema)  # a saved database (5.4)
                    if self._source is not None
                    else frozenset(p for layer in self._semantic_layers for p in layer.pii_columns)
                )
                catalog = SchemaCatalog(schema, self._semantic_layers, self._examples)
                self._schema = (catalog, self._make_retriever(catalog))
            return self._schema

    def table_names(self) -> list[str]:
        """Every table of this database the read-only login can read (reads the schema once)."""
        catalog, _ = self._schema_info()
        return sorted(catalog.schema.table_names)

    def _view(
        self, allowed: frozenset[str] | None, mask_pii: bool = False, *, scoped: bool = False
    ) -> View:
        """The database as one user sees it: only `allowed` tables (None = every table, 5.2),
        with personal data masked if `mask_pii` (5.4), and without the sample rows of row-secured
        tables if the user sees only some rows (`scoped`, 5.3).

        Cached by the tables really visible (`allowed` ∩ the schema), so names in a rule that no
        longer exist don't make a new view.

        Raises:
            NoPermittedTablesError: no table of this database is allowed.
        """
        catalog, retriever = self._schema_info()
        everything = catalog.schema.table_names
        visible = everything if allowed is None else frozenset(allowed & everything)
        whole = visible == everything
        if whole and not mask_pii and not scoped:
            return catalog, retriever, None
        if not visible:
            raise NoPermittedTablesError(
                "You can't use any table of this database. Ask an admin of your workspace "
                "for access."
            )
        key = (visible, mask_pii, scoped)
        with self._views_lock:
            cached = self._views.get(key)
            if cached is not None:
                self._views.move_to_end(key)
                return cached
            seen = catalog if whole else catalog.restricted(visible)
            guard = PiiGuard(seen.schema, self._personal) if mask_pii else None
            if guard is not None:
                seen = seen.masked(guard)
            if scoped:
                seen = seen.scoped()
            settings = self._settings
            # A few visible tables are sent whole, as for a small database: no recall risk.
            searches = retriever is not None and (
                whole
                or settings.schema_retrieval == "on"
                or len(visible) > settings.retrieval_min_tables
            )
            view = (seen, retriever.narrowed(seen) if retriever and searches else None, guard)
            self._views[key] = view
            while len(self._views) > MAX_VIEWS:
                self._views.popitem(last=False)
            return view

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
        if self._saved_index is not None:
            return self._saved_database_retriever(catalog, self._saved_index)
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

    def _saved_database_retriever(
        self, catalog: SchemaCatalog, index: SavedDatabaseIndex
    ) -> SchemaRetriever | None:
        """A saved database's retriever (5.10b): keyword search over its documents in the app
        database. Synced first: hashes compared, nothing embedded, an unchanged schema writes
        nothing. A new dictionary version rebuilds the pipeline (registry), so its first question
        indexes the new descriptions with no separate trigger.

        Raises:
            SchemaIndexError: SCHEMA_RETRIEVAL=on, but the index can't be synced.
        """
        # The dictionary is already in the catalog's layer (_schema_info), so no dictionaries here.
        docs = build_documents(catalog.schema, catalog.layers, {})
        try:
            with index.connect(self._settings) as conn:
                written, deleted = sync_connection_documents(conn, index.connection_id, docs)
        except (psycopg.Error, DatabaseUnavailableError) as exc:
            if self._settings.schema_retrieval == "on":
                raise SchemaIndexError(f"Schema retrieval can't run: {exc}") from exc
            logger.warning(
                "schema index sync failed, sending the whole schema", extra={"error": str(exc)}
            )
            return None
        if written or deleted:
            logger.info(
                "schema index synced",
                extra={
                    "connection_id": str(index.connection_id),
                    "written": written,
                    "deleted": deleted,
                },
            )
        return SchemaRetriever(
            catalog=catalog,
            embeddings=None,
            connect=index.connect,  # the app database: the user's holds no index of ours
            settings=self._settings,
            connection_id=index.connection_id,
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
        provider: str | None = None,
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
                "provider": provider or self._llm.name,
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
