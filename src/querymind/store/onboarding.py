"""Onboarding a saved database (5.10): the run's progress and the drafted data dictionary.

    store = OnboardingStore(settings)
    store.queue(connection_id)                       # a new run (refused while one is running)
    store.progress(connection_id, status="drafting", total=40, drafted=12)
    store.finish(connection_id, merge, generated_by="groq / openai/gpt-oss-120b", skipped=0)
    store.dictionary_for(connection_id)              # → (dictionary, version) for the pipeline
    store.edit(workspace_id, connection_id, edited)  # an admin's review

One row per database (`querymind_app.connection_dictionaries`). `dictionary_version` changes only
when the content does (a finished run, an edit): the pipeline registry rebuilds on it, not on
every progress update. A finished run merges into the *stored* dictionary under a row lock, so an
edit is never overwritten by a run that read the dictionary before it.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.semantic.dictionary import DataDictionary
from querymind.store.errors import (
    DictionaryNotFoundError,
    OnboardingRunningError,
    StoreUnavailableError,
)

logger = logging.getLogger(__name__)

OnboardingStatus = Literal["queued", "reading", "drafting", "ready", "failed"]
RUNNING: tuple[OnboardingStatus, ...] = ("queued", "reading", "drafting")

_COLUMNS = """
    connection_id, status, tables_total, tables_drafted, tables_skipped, error, generated_by,
    dictionary, dictionary_version, started_at, updated_at
"""
_RUNNING_MESSAGE = "This database is being onboarded right now. Try again when it has finished."


class OnboardingRecord(BaseModel):
    """Where a database's onboarding is, and its dictionary."""

    model_config = ConfigDict(frozen=True)

    connection_id: UUID
    status: OnboardingStatus
    tables_total: int
    tables_drafted: int
    tables_skipped: int  # not described: names that aren't plain lowercase, or over the cap
    error: str | None
    generated_by: str
    dictionary: DataDictionary
    dictionary_version: int  # 0 = no dictionary yet
    started_at: datetime
    updated_at: datetime


def _record(row: tuple[object, ...]) -> OnboardingRecord:
    names = [name.strip() for name in _COLUMNS.split(",")]
    values = dict(zip(names, row, strict=True))
    raw = values["dictionary"] or {}
    values["dictionary"] = (
        DataDictionary.model_validate(raw) if raw else DataDictionary(database="")
    )
    return OnboardingRecord.model_validate(values)


class OnboardingStore:
    """Reads and writes `querymind_app.connection_dictionaries`."""

    def __init__(
        self,
        settings: Settings,
        *,
        connect: Callable[[Settings], psycopg.Connection] = connect_app,
    ) -> None:
        self._settings = settings
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("onboarding store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Onboarding isn't available right now.") from exc

    def queue(self, connection_id: UUID) -> OnboardingRecord:
        """Start a new run: status `queued`, counts reset, the dictionary kept.

        Raises:
            OnboardingRunningError: a run is already queued or running.
        """
        with self._transaction() as conn:
            row = conn.execute(
                f"""
                INSERT INTO querymind_app.connection_dictionaries (connection_id, status)
                VALUES (%(id)s, 'queued')
                ON CONFLICT (connection_id) DO UPDATE SET
                    status = 'queued', tables_total = 0, tables_drafted = 0, tables_skipped = 0,
                    error = NULL, started_at = now(), updated_at = now()
                WHERE connection_dictionaries.status <> ALL(%(running)s)
                RETURNING {_COLUMNS}
                """.encode(),
                {"id": connection_id, "running": list(RUNNING)},
            ).fetchone()
        if row is None:
            raise OnboardingRunningError(_RUNNING_MESSAGE)
        return _record(row)

    def progress(
        self,
        connection_id: UUID,
        *,
        status: OnboardingStatus,
        total: int | None = None,
        drafted: int | None = None,
        skipped: int | None = None,
    ) -> bool:
        """Record how far the run is. False when the row is gone (the database was deleted)."""
        with self._transaction() as conn:
            updated = conn.execute(
                """
                UPDATE querymind_app.connection_dictionaries SET
                    status = %(status)s,
                    tables_total = COALESCE(%(total)s, tables_total),
                    tables_drafted = COALESCE(%(drafted)s, tables_drafted),
                    tables_skipped = COALESCE(%(skipped)s, tables_skipped),
                    updated_at = now()
                WHERE connection_id = %(id)s
                """,
                {
                    "id": connection_id,
                    "status": status,
                    "total": total,
                    "drafted": drafted,
                    "skipped": skipped,
                },
            ).rowcount
        return updated > 0

    def finish(
        self,
        connection_id: UUID,
        merge: Callable[[DataDictionary | None], DataDictionary],
        *,
        generated_by: str,
        skipped: int,
        drafted: int = 0,
    ) -> bool:
        """Merge the run's drafts into the stored dictionary (locked: an edit made during the run
        is kept) and mark it ready. False when the row is gone."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT dictionary FROM querymind_app.connection_dictionaries"
                " WHERE connection_id = %s FOR UPDATE",
                (connection_id,),
            ).fetchone()
            if row is None:
                return False
            stored = DataDictionary.model_validate(row[0]) if row[0] else None
            merged = merge(stored)
            conn.execute(
                """
                UPDATE querymind_app.connection_dictionaries SET
                    status = 'ready', error = NULL, generated_by = %s, dictionary = %s,
                    dictionary_version = dictionary_version + 1, tables_skipped = %s,
                    tables_drafted = %s, updated_at = now()
                WHERE connection_id = %s
                """,
                (generated_by, Jsonb(merged.model_dump()), skipped, drafted, connection_id),
            )
        return True

    def save_drafts(
        self, connection_id: UUID, merge: Callable[[DataDictionary | None], DataDictionary]
    ) -> bool:
        """Save a run's drafts so far (merged under the row lock), without marking it ready: a run
        stopped later keeps them. False when the row is gone."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT dictionary FROM querymind_app.connection_dictionaries"
                " WHERE connection_id = %s FOR UPDATE",
                (connection_id,),
            ).fetchone()
            if row is None:
                return False
            stored = DataDictionary.model_validate(row[0]) if row[0] else None
            conn.execute(
                "UPDATE querymind_app.connection_dictionaries SET dictionary = %s,"
                " updated_at = now() WHERE connection_id = %s",
                (Jsonb(merge(stored).model_dump()), connection_id),
            )
        return True

    def stored_dictionary(self, connection_id: UUID) -> DataDictionary | None:
        """Whatever is stored, finished or not: what a new run builds on."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT dictionary FROM querymind_app.connection_dictionaries"
                " WHERE connection_id = %s",
                (connection_id,),
            ).fetchone()
        return DataDictionary.model_validate(row[0]) if row and row[0] else None

    def fail(self, connection_id: UUID, error: str) -> None:
        """Mark the run failed with a message safe to show. Drafts saved so far are kept."""
        with self._transaction() as conn:
            conn.execute(
                "UPDATE querymind_app.connection_dictionaries"
                " SET status = 'failed', error = %s, updated_at = now() WHERE connection_id = %s",
                (error[:500], connection_id),
            )

    def get(self, workspace_id: UUID, connection_id: UUID) -> OnboardingRecord | None:
        """The run and dictionary of one of this workspace's databases (None: never onboarded)."""
        with self._transaction() as conn:
            row = conn.execute(
                f"""
                SELECT {_COLUMNS} FROM querymind_app.connection_dictionaries AS d
                WHERE d.connection_id = %s AND EXISTS (
                    SELECT 1 FROM querymind_app.connections AS c
                    WHERE c.id = d.connection_id AND c.workspace_id = %s
                )
                """.encode(),
                (connection_id, workspace_id),
            ).fetchone()
        return _record(row) if row is not None else None

    def dictionary_for(self, connection_id: UUID) -> tuple[DataDictionary, int] | None:
        """The dictionary and its version, for the pipeline (the caller has checked the
        workspace). None while there is none."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT dictionary, dictionary_version FROM querymind_app.connection_dictionaries"
                " WHERE connection_id = %s AND dictionary_version > 0",
                (connection_id,),
            ).fetchone()
        if row is None:
            return None
        return DataDictionary.model_validate(row[0]), int(row[1])

    def edit(
        self, workspace_id: UUID, connection_id: UUID, dictionary: DataDictionary
    ) -> OnboardingRecord:
        """Replace the dictionary with an admin's reviewed version.

        Raises:
            DictionaryNotFoundError: the database has no dictionary (or isn't this workspace's).
            OnboardingRunningError: a run is going on: its result would overwrite the edit.
        """
        current = self.get(workspace_id, connection_id)
        if current is None or current.dictionary_version == 0:
            raise DictionaryNotFoundError("This database has no data dictionary yet: onboard it.")
        with self._transaction() as conn:
            row = conn.execute(
                f"""
                UPDATE querymind_app.connection_dictionaries SET
                    dictionary = %(dictionary)s, dictionary_version = dictionary_version + 1,
                    updated_at = now()
                WHERE connection_id = %(id)s AND status <> ALL(%(running)s)
                RETURNING {_COLUMNS}
                """.encode(),
                {
                    "id": connection_id,
                    "dictionary": Jsonb(dictionary.model_dump()),
                    "running": list(RUNNING),
                },
            ).fetchone()
        if row is None:
            raise OnboardingRunningError(_RUNNING_MESSAGE)
        return _record(row)

    def mark_interrupted(self) -> int:
        """At start-up: runs left queued or running by a stopped server are failed, not stuck."""
        with self._transaction() as conn:
            count = conn.execute(
                """
                UPDATE querymind_app.connection_dictionaries
                SET status = 'failed', error = 'Interrupted by a server restart: onboard it again.',
                    updated_at = now()
                WHERE status = ANY(%s)
                """,
                (list(RUNNING),),
            ).rowcount
        return count
