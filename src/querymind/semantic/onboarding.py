"""Onboarding a saved database (5.10): read its schema, draft its data dictionary, store it.

    onboarder = Onboarder(store, connections, llms, allow_private=False, max_tables=100)
    onboarder.start(workspace_id, connection_id)   # queued, then runs in the background
    store.get(workspace_id, connection_id)         # queued → reading → drafting → ready | failed

A fresh database has no semantic layer: table names alone ("tbl_cust", "customer" next to
"customers") leave the LLM guessing. Onboarding asks the workspace's **own** LLM (5.8: own
database, own key) to describe each table and column from its columns, keys and 3 sample rows,
the same `draft_table` as the demo's dictionary (3.2). The result becomes the database's semantic
layer (`layer_from_dictionary`), so every later prompt says what each table means.

Runs in a small thread pool, so saving a database returns at once. Each run:
- drafts only tables that still have no description (reviewed ones are never touched), at most
  `max_tables`, so a run stopped by a rate limit or a restart picks up where it left off;
- waits out rate limits (the provider's Retry-After) and short outages (5, 10, 20, 40 s);
- keeps what it drafted when it fails, so the next run only fills the gaps;
- skips a table whose answer doesn't fit the schema, and counts it;
- stops at once on a refused key, and never leaves a run "running": any failure is recorded.
"""

import logging
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from querymind.db.remote import connect_remote
from querymind.db.schema import SchemaInfo, Table, read_schema
from querymind.llm import (
    LLMAuthError,
    LLMOutputError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
)
from querymind.llm.workspace import WorkspaceLLMs
from querymind.semantic.dictionary import DataDictionary, TableDraft, draft_table, merge_drafts
from querymind.semantic.models import NAME, SemanticLayer
from querymind.store.connections import ConnectionRecord, ConnectionStore
from querymind.store.errors import ConnectionNotFoundError, StoreError
from querymind.store.onboarding import OnboardingRecord, OnboardingStore

logger = logging.getLogger(__name__)

WORKERS = 2  # onboardings at the same time; more wait their turn ("queued")
SAVE_EVERY = 10  # drafts are saved this often, so a stopped run loses little
RATE_LIMIT_WAITS = 4  # times a rate-limited table is retried before the run fails
MAX_WAIT_SECONDS = 60.0
OUTAGE_FIRST_WAIT_SECONDS = 5.0  # then doubled: when the provider didn't say how long

NO_KEY = "Save an LLM key for this workspace first: onboarding drafts the dictionary with it."


def needs_draft(table: Table, stored: DataDictionary | None) -> bool:
    """A table still without a description, and not reviewed by a person."""
    entry = stored.tables.get(table.name) if stored else None
    return entry is None or (not entry.reviewed and not entry.description)


def plain_name(name: str) -> bool:
    """Lowercase SQL names only can be described in a semantic layer (models.NAME)."""
    return re.fullmatch(NAME, name) is not None


class Onboarder:
    """Starts onboarding runs and does them in the background. Thread-safe."""

    def __init__(
        self,
        store: OnboardingStore,
        connections: ConnectionStore,
        llms: WorkspaceLLMs,
        *,
        allow_private: bool = False,
        max_tables: int = 100,
        describe_error: Callable[[Exception], str] = lambda exc: "Onboarding failed.",
        on_ready: Callable[[UUID], None] = lambda connection_id: None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._store = store
        self._connections = connections
        self._llms = llms
        self._allow_private = allow_private
        self._max_tables = max_tables
        self._describe_error = describe_error  # a message safe to show (the API's wording)
        self._on_ready = on_ready  # e.g. drop the database's cached pipeline
        self._sleep = sleep
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="onboarding")

    def start(self, workspace_id: UUID, connection_id: UUID) -> OnboardingRecord:
        """Queue a run and return at once.

        Raises:
            OnboardingRunningError: one is already queued or running for this database.
        """
        record = self._store.queue(connection_id)
        self._pool.submit(self.run, workspace_id, connection_id)
        logger.info("onboarding queued", extra={"connection_id": str(connection_id)})
        return record

    def shutdown(self) -> None:
        """At server stop: queued runs are dropped (start-up marks them failed)."""
        self._pool.shutdown(wait=False, cancel_futures=True)

    def run(self, workspace_id: UUID, connection_id: UUID) -> None:
        """One whole run. Never raises: a failure is recorded on the run, for the user to see."""
        try:
            self._run(workspace_id, connection_id)
        except ConnectionNotFoundError:
            logger.info("onboarding stopped: database deleted", extra={"id": str(connection_id)})
        except Exception as exc:
            logger.warning(
                "onboarding failed",
                extra={"connection_id": str(connection_id), "error": type(exc).__name__},
            )
            try:
                self._store.fail(connection_id, self._describe_error(exc))
            except StoreError:
                logger.warning("onboarding failure not recorded", extra={"id": str(connection_id)})

    def _run(self, workspace_id: UUID, connection_id: UUID) -> None:
        record = self._connections.get(workspace_id, connection_id)
        llm = self._llms.provider_for(workspace_id)
        if llm is None:
            self._store.fail(connection_id, NO_KEY)
            return
        if not self._store.progress(connection_id, status="reading"):
            return  # deleted meanwhile
        password = self._connections.password(workspace_id, record)
        with connect_remote(record.spec(), password, allow_private=self._allow_private) as conn:
            schema = read_schema(conn)

        current = self._store.stored_dictionary(connection_id)  # drafts of earlier runs too
        describable = [t for t in schema.tables if plain_name(t.name)]
        missing = [t for t in describable if needs_draft(t, current)]
        todo = missing[: self._max_tables]
        not_described = (len(schema.tables) - len(describable)) + (len(missing) - len(todo))
        if not self._store.progress(connection_id, status="drafting", total=len(todo), drafted=0):
            return

        layer = SemanticLayer(
            database=record.name,
            version=1,
            description=f"The database {record.dbname} of this workspace.",
        )
        generated_by = llm.name
        drafts: dict[str, TableDraft] = {}
        failed = 0
        for number, table in enumerate(todo, start=1):
            try:
                draft = self._draft(llm, table, layer)
            except Exception:
                # Keep what was drafted: the next run only fills the gaps.
                if drafts:
                    self._save(connection_id, record, schema, drafts, generated_by)
                raise
            if draft is None:
                failed += 1
            else:
                drafts[table.name] = draft
            if number % SAVE_EVERY == 0 and number < len(todo):
                # Saved as we go: a run stopped later (rate limit, restart) keeps these.
                if not self._save(connection_id, record, schema, drafts, generated_by):
                    return
                if not self._store.progress(connection_id, status="drafting", drafted=number):
                    return
        merge = _merger(record, schema, drafts, generated_by)
        if self._store.finish(
            connection_id,
            merge,
            generated_by=generated_by,
            skipped=not_described + failed,
            drafted=len(drafts),
        ):
            self._on_ready(connection_id)
            logger.info(
                "onboarding ready",
                extra={"connection_id": str(connection_id), "drafted": len(drafts)},
            )

    def _draft(self, llm: LLMProvider, table: Table, layer: SemanticLayer) -> TableDraft | None:
        """One table's draft, waiting out rate limits and short outages. None: the answer didn't
        fit the schema (that table is skipped).

        A provider that is overloaded ("503 high demand", seen live with Gemini in 5.10) or briefly
        unreachable usually recovers within a minute: waiting beats failing a 100-table run.

        Raises:
            LLMAuthError: the key was refused (waiting won't help).
            LLMProviderError / LLMRateLimitError: still failing after RATE_LIMIT_WAITS waits.
        """
        for attempt in range(RATE_LIMIT_WAITS + 1):
            try:
                return draft_table(llm, table, layer)
            except LLMOutputError:
                return None
            except LLMAuthError:
                raise
            except LLMProviderError as exc:  # a rate limit, an overload, a network blip
                if attempt == RATE_LIMIT_WAITS:
                    raise
                if isinstance(exc, LLMRateLimitError) and exc.retry_after_seconds:
                    wait = min(exc.retry_after_seconds, MAX_WAIT_SECONDS)
                else:  # 5 s, 10 s, 20 s, 40 s
                    wait = min(OUTAGE_FIRST_WAIT_SECONDS * 2**attempt, MAX_WAIT_SECONDS)
                logger.info(
                    "onboarding waits for the provider",
                    extra={"seconds": wait, "error": type(exc).__name__},
                )
                self._sleep(wait)
        return None  # not reached: the loop returns or raises

    def _save(
        self,
        connection_id: UUID,
        record: ConnectionRecord,
        schema: SchemaInfo,
        drafts: dict[str, TableDraft],
        generated_by: str,
    ) -> bool:
        """Save the drafts so far without marking the run ready."""
        return self._store.save_drafts(connection_id, _merger(record, schema, drafts, generated_by))


def _merger(
    record: ConnectionRecord, schema: SchemaInfo, drafts: dict[str, TableDraft], generated_by: str
) -> Callable[[DataDictionary | None], DataDictionary]:
    """Merge these drafts into whatever is stored *then* (reviewed tables are kept as they are)."""

    def merge(stored: DataDictionary | None) -> DataDictionary:
        return merge_drafts(
            stored, drafts, database=record.name, schema=schema, generated_by=generated_by
        )

    return merge
