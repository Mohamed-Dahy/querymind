"""Saved conversations, in their own schema, through their own database role.

    store = ConversationStore(settings)
    store.save_turn(conversation_id, question=..., kind="answer", sql=..., reply=..., trace=[...])
    store.get(conversation_id).turns

What is saved is the API's reply, without its result rows: they may hold personal data, so they are
removed here and the database refuses them too (a CHECK in data/setup_app_db.py). A re-run fetches
fresh rows instead. The answer text and the SQL are kept, and can still quote values until PII
masking arrives (5.4).

Every query has its parameters passed separately, so nothing a user typed can become SQL.
"""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from querymind.agent.schemas import Clarification, Turn
from querymind.agent.state import MAX_HISTORY_TURNS
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.store.errors import (
    ConversationNotFoundError,
    StoreError,
    StoreUnavailableError,
)

logger = logging.getLogger(__name__)

TITLE_LENGTH = 80  # a title is the first question, cut to fit the history list

_SUMMARY_COLUMNS = """
    id, title, pinned, created_at, updated_at,
    (SELECT count(*) FROM querymind_app.turns AS t WHERE t.conversation_id = c.id) AS turns
"""


class ConversationSummary(BaseModel):
    """One line of the history list."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    pinned: bool
    created_at: datetime
    updated_at: datetime
    turns: int


class SavedTurn(BaseModel):
    """One question of a conversation and what came back, as it was saved."""

    model_config = ConfigDict(frozen=True)

    n: int
    question: str
    kind: Literal["answer", "clarification", "refusal"]
    sql: str | None
    reply: dict[str, Any]  # the API's reply, without `rows`
    trace: list[dict[str, Any]]  # the live events of that run; empty if it didn't stream
    created_at: datetime


class Conversation(ConversationSummary):
    """A conversation with its turns, oldest first."""

    saved_turns: list[SavedTurn]


def title_from(question: str) -> str:
    """A short title from a question: its first line, cut at a word if it is long."""
    line = question.strip().splitlines()[0].strip() if question.strip() else "Untitled"
    if len(line) <= TITLE_LENGTH:
        return line
    cut = line[:TITLE_LENGTH].rsplit(" ", 1)[0]
    return f"{cut}…"


def _like_pattern(text: str) -> str:
    """`text` found anywhere, with LIKE's own wildcards meaning themselves."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class ConversationStore:
    """Reads and writes saved conversations. One new connection per call, like the agent's."""

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
        """A connection for one transaction: committed at the end, rolled back on an error."""
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("conversation store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Saved conversations aren't available right now.") from exc

    def save_turn(
        self,
        conversation_id: UUID,
        *,
        question: str,
        kind: Literal["answer", "clarification", "refusal"],
        sql: str | None,
        reply: Mapping[str, Any],
        trace: Sequence[Mapping[str, Any]] = (),
    ) -> int:
        """Save one turn, creating the conversation on its first. Returns the turn's number.

        The upsert locks the conversation's row until this transaction ends. So when two saves of
        one conversation run at once the second waits, and then `MAX(n) + 1` sees the first one's
        turn: the numbers can't collide.
        """
        without_rows = {key: value for key, value in reply.items() if key != "rows"}
        with self._transaction() as conn:
            conn.execute(
                """
                INSERT INTO querymind_app.conversations (id, title) VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE SET updated_at = now()
                """,
                (conversation_id, title_from(question)),
            )
            row = conn.execute(
                """
                INSERT INTO querymind_app.turns
                    (conversation_id, n, question, kind, sql, reply, trace)
                SELECT %(id)s, COALESCE(MAX(n), 0) + 1, %(question)s, %(kind)s, %(sql)s,
                       %(reply)s, %(trace)s
                FROM querymind_app.turns WHERE conversation_id = %(id)s
                RETURNING n
                """,
                {
                    "id": conversation_id,
                    "question": question,
                    "kind": kind,
                    "sql": sql,
                    "reply": Jsonb(without_rows),
                    "trace": Jsonb(list(trace)),
                },
            ).fetchone()
        if row is None:  # INSERT … SELECT with an aggregate always makes one row
            raise StoreUnavailableError("The turn wasn't saved.")
        number: int = row[0]
        return number

    def list_conversations(
        self, *, search: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ConversationSummary], bool]:
        """Conversations, pinned first, then the most recent, and whether there are more.

        `search` looks in the titles and in every question.
        """
        query = f"""
            SELECT {_SUMMARY_COLUMNS}
            FROM querymind_app.conversations AS c
            WHERE %(search)s::text IS NULL
               OR c.title ILIKE %(like)s ESCAPE '\\'
               OR EXISTS (
                    SELECT 1 FROM querymind_app.turns AS t
                    WHERE t.conversation_id = c.id AND t.question ILIKE %(like)s ESCAPE '\\'
               )
            ORDER BY c.pinned DESC, c.updated_at DESC, c.id
            LIMIT %(limit)s OFFSET %(offset)s
        """
        with self._transaction() as conn:
            found = (
                conn.cursor(row_factory=class_row(ConversationSummary))
                .execute(
                    query.encode(),
                    {
                        "search": search,
                        "like": _like_pattern(search or ""),
                        "limit": limit + 1,  # one extra says whether another page exists
                        "offset": offset,
                    },
                )
                .fetchall()
            )
        return found[:limit], len(found) > limit

    def get(self, conversation_id: UUID) -> Conversation:
        """One conversation with all its turns.

        Raises:
            ConversationNotFoundError: no conversation has this id.
        """
        with self._transaction() as conn:
            summary = self._summary(conn, conversation_id)
            turns = self._turns(conn, conversation_id)
        return Conversation(**summary.model_dump(), saved_turns=turns)

    def recent_turns(self, conversation_id: UUID, *, limit: int) -> list[SavedTurn]:
        """The last `limit` turns, oldest first; none if the conversation doesn't exist."""
        with self._transaction() as conn:
            return self._turns(conn, conversation_id)[-limit:]

    def update(
        self, conversation_id: UUID, *, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary:
        """Rename or pin a conversation. `updated_at` stays: this isn't new activity in it.

        Raises:
            ConversationNotFoundError: no conversation has this id.
        """
        query = f"""
            UPDATE querymind_app.conversations AS c
            SET title = COALESCE(%(title)s, title), pinned = COALESCE(%(pinned)s, pinned)
            WHERE id = %(id)s
            RETURNING {_SUMMARY_COLUMNS}
        """
        with self._transaction() as conn:
            found = (
                conn.cursor(row_factory=class_row(ConversationSummary))
                .execute(
                    query.encode(),
                    {"id": conversation_id, "title": title, "pinned": pinned},
                )
                .fetchone()
            )
        if found is None:
            raise ConversationNotFoundError(str(conversation_id))
        return found

    def delete(self, conversation_id: UUID) -> None:
        """Delete a conversation and its turns for good.

        Raises:
            ConversationNotFoundError: no conversation has this id.
        """
        with self._transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM querymind_app.conversations WHERE id = %s RETURNING id",
                (conversation_id,),
            ).fetchone()
        if deleted is None:
            raise ConversationNotFoundError(str(conversation_id))

    def _summary(self, conn: psycopg.Connection, conversation_id: UUID) -> ConversationSummary:
        query = f"""
            SELECT {_SUMMARY_COLUMNS} FROM querymind_app.conversations AS c WHERE id = %s
        """
        found = (
            conn.cursor(row_factory=class_row(ConversationSummary))
            .execute(query.encode(), (conversation_id,))
            .fetchone()
        )
        if found is None:
            raise ConversationNotFoundError(str(conversation_id))
        return found

    def _turns(self, conn: psycopg.Connection, conversation_id: UUID) -> list[SavedTurn]:
        return (
            conn.cursor(row_factory=class_row(SavedTurn))
            .execute(
                """
                SELECT n, question, kind, sql, reply, trace, created_at
                FROM querymind_app.turns WHERE conversation_id = %s ORDER BY n
                """,
                (conversation_id,),
            )
            .fetchall()
        )


def turn_from_saved(saved: SavedTurn) -> Turn:
    """What the agent's memory holds about a saved turn: the same as `turn_from` for a live one."""
    reply = saved.reply
    match saved.kind:
        case "answer":
            return Turn(
                question=saved.question,
                kind="answer",
                sql=saved.sql,
                columns=reply["columns"],
                row_count=reply["row_count"],
            )
        case "clarification":
            asked = Clarification(question=reply["clarifying_question"], options=reply["options"])
            return Turn(question=saved.question, kind="clarification", reply=asked.memory_text())
        case "refusal":
            return Turn(question=saved.question, kind="refusal", reply=reply["message"])


def load_history(store: ConversationStore, conversation_id: str) -> list[Turn]:
    """A conversation's last turns as the agent's memory. If the store fails, no memory.

    A follow-up may then read less well, but the question is still answered.
    """
    try:
        turns = store.recent_turns(UUID(conversation_id), limit=MAX_HISTORY_TURNS)
    except StoreError:
        return []
    return [turn_from_saved(turn) for turn in turns]


def create_store(settings: Settings) -> ConversationStore | None:
    """The store, or None when APP_DB_USER isn't set: history is then simply off."""
    return ConversationStore(settings) if settings.history_enabled else None
