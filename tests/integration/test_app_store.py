"""The app's storage on the real database: who can reach what, and that saving really works.

Needs the setup too: `uv run python data/setup_app_db.py`, with APP_DB_USER and APP_DB_PASSWORD set.
Skipped without them, because history is optional. CI sets them, so CI always runs these.
"""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors
from setup_app_db import SCHEMA, set_up

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.schema import introspect_schema
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.store.conversations import ConversationStore, SavedTurn
from querymind.store.errors import ConversationNotFoundError

pytestmark = pytest.mark.integration

APP_TABLES = ("conversations", "turns", "saved_questions")
REPLY = {
    "kind": "answer",
    "question": "Top customers?",
    "answer": "Two customers lead.",
    "columns": ["company_name"],
    "rows": [["Alfreds Futterkiste"], ["Ernst Handel"]],
    "row_count": 2,
}


@pytest.fixture
def store(settings: Settings) -> ConversationStore:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: history is off (see .env.example)")
    return ConversationStore(settings)


@pytest.fixture
def conversation(store: ConversationStore) -> Iterator[UUID]:
    """A new conversation id, deleted again after the test."""
    conversation_id = uuid4()
    yield conversation_id
    with suppress(ConversationNotFoundError):  # the test never saved one, or deleted it itself
        store.delete(conversation_id)


@pytest.fixture
def app_conn(settings: Settings, store: ConversationStore) -> Iterator[psycopg.Connection]:
    """Connection as the app's role. Closing without commit undoes anything a test did."""
    conn = connect_app(settings)
    try:
        yield conn
    finally:
        conn.close()


def save(store: ConversationStore, conversation_id: UUID, question: str = "Top customers?") -> int:
    return store.save_turn(
        conversation_id,
        question=question,
        kind="answer",
        sql="SELECT company_name FROM customers",
        reply=REPLY,
        trace=[{"type": "step", "node": "generate_sql", "status": "started"}],
    )


# --- Who can reach what -------------------------------------------------------------------------


@pytest.mark.parametrize("statement", ["SELECT * FROM orders", "SELECT * FROM public.customers"])
def test_the_app_role_cant_read_business_tables(
    app_conn: psycopg.Connection, statement: str
) -> None:
    with pytest.raises((errors.InsufficientPrivilege, errors.UndefinedTable)):
        app_conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        f"DROP TABLE {SCHEMA}.turns",
        f"TRUNCATE {SCHEMA}.turns",
        f"ALTER TABLE {SCHEMA}.turns ADD COLUMN hacked integer",
        f"CREATE TABLE {SCHEMA}.stolen (id integer)",
        "CREATE ROLE hacker",
    ],
)
def test_the_app_role_can_only_change_rows(app_conn: psycopg.Connection, statement: str) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        app_conn.execute(statement)


@pytest.mark.parametrize("table", APP_TABLES)
def test_the_agents_role_cant_read_the_app_tables(
    ro_conn: psycopg.Connection, store: ConversationStore, table: str
) -> None:
    with pytest.raises(errors.InsufficientPrivilege):
        ro_conn.execute(f"SELECT * FROM {SCHEMA}.{table}")


@pytest.mark.parametrize("table", ["turns", f"{SCHEMA}.turns", f'"{SCHEMA}"."turns"'])
def test_the_validator_refuses_the_app_tables_even_if_allowed_by_name(table: str) -> None:
    with pytest.raises(UnsafeSqlError):
        validate_sql(f"SELECT * FROM {table}", allowed_tables={"customers"}, max_rows=10)
    if table != "turns":
        # A schema-qualified name is refused for its schema, whatever the allowlist says.
        with pytest.raises(UnsafeSqlError):
            validate_sql(f"SELECT * FROM {table}", allowed_tables={"turns"}, max_rows=10)


def test_the_schema_the_llm_sees_doesnt_list_the_app_tables(
    ro_conn: psycopg.Connection, store: ConversationStore
) -> None:
    names = introspect_schema(ro_conn).table_names

    assert names.isdisjoint(APP_TABLES)


def test_setup_can_run_again_and_keeps_what_is_saved(
    settings: Settings, store: ConversationStore, conversation: UUID
) -> None:
    save(store, conversation)
    assert settings.app_db_user is not None and settings.app_db_password is not None

    # Rolled back at the end: this test must not change the password other tests connect with.
    with psycopg.connect(settings.admin_conninfo()) as admin:
        set_up(
            admin,
            db_name=settings.postgres_db,
            user=settings.app_db_user,
            password=settings.app_db_password.get_secret_value(),
        )
        row = admin.execute(
            f"SELECT count(*) FROM {SCHEMA}.turns WHERE conversation_id = %s",
            (conversation,),
        ).fetchone()
        admin.rollback()

    assert row == (1,)


# --- Rows stay out ------------------------------------------------------------------------------


def test_no_row_values_are_saved(
    store: ConversationStore, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, conversation)

    row = app_conn.execute(
        f"SELECT reply::text, trace::text FROM {SCHEMA}.turns WHERE conversation_id = %s",
        (conversation,),
    ).fetchone()

    assert row is not None
    assert "Alfreds Futterkiste" not in row[0] + row[1]
    assert "rows" not in store.get(conversation).saved_turns[0].reply
    assert store.get(conversation).saved_turns[0].reply["row_count"] == 2


def test_the_database_itself_refuses_rows(
    store: ConversationStore, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, conversation)

    with pytest.raises(errors.CheckViolation):
        app_conn.execute(
            f"""INSERT INTO {SCHEMA}.turns (conversation_id, n, question, kind, reply)
                VALUES (%s, 99, 'q', 'answer', '{{"rows": [[1]]}}')""",
            (conversation,),
        )


# --- Saving and reading -------------------------------------------------------------------------


def test_turns_are_numbered_and_the_first_question_is_the_title(
    store: ConversationStore, conversation: UUID
) -> None:
    assert save(store, conversation, "Top customers?") == 1
    assert save(store, conversation, "Now only for 1997") == 2

    saved = store.get(conversation)

    assert saved.title == "Top customers?"
    assert saved.turns == 2
    assert [turn.question for turn in saved.saved_turns] == ["Top customers?", "Now only for 1997"]
    first: SavedTurn = saved.saved_turns[0]
    assert first.sql == "SELECT company_name FROM customers"
    assert first.trace[0]["node"] == "generate_sql"


def test_saves_at_the_same_time_get_different_numbers(
    store: ConversationStore, conversation: UUID
) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(lambda _: save(store, conversation), range(8)))

    assert sorted(numbers) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_a_new_turn_moves_the_conversation_up_but_renaming_doesnt(
    store: ConversationStore, conversation: UUID
) -> None:
    other = uuid4()
    try:
        save(store, conversation)
        save(store, other)
        store.update(conversation, title="Renamed")
        assert store.list_conversations(limit=2)[0][0].id == other

        save(store, conversation)
        assert store.list_conversations(limit=2)[0][0].id == conversation
    finally:
        store.delete(other)


def test_pinned_comes_first(store: ConversationStore, conversation: UUID) -> None:
    other = uuid4()
    try:
        save(store, conversation)
        save(store, other)
        store.update(conversation, pinned=True)

        found, _ = store.list_conversations(limit=1)

        assert found[0].id == conversation and found[0].pinned
    finally:
        store.delete(other)


def test_paging_says_when_there_is_more(store: ConversationStore, conversation: UUID) -> None:
    other = uuid4()
    try:
        save(store, conversation, "paging-marker one")
        save(store, other, "paging-marker two")

        first, more = store.list_conversations(search="paging-marker", limit=1)
        second, last = store.list_conversations(search="paging-marker", limit=1, offset=1)

        assert len(first) == 1 and more
        assert len(second) == 1 and not last
        assert first[0].id != second[0].id
    finally:
        store.delete(other)


def test_search_looks_in_titles_and_in_every_question(
    store: ConversationStore, conversation: UUID
) -> None:
    save(store, conversation, "First question")
    save(store, conversation, "Something about ZEBRA sales")

    assert [c.id for c in store.list_conversations(search="zebra")[0]] == [conversation]
    store.update(conversation, title="QUOKKA report")
    assert [c.id for c in store.list_conversations(search="quokka")[0]] == [conversation]


@pytest.mark.parametrize("search", ["%", "_", "\\", "100%_\\"])
def test_search_treats_wildcards_as_plain_text(
    store: ConversationStore, conversation: UUID, search: str
) -> None:
    save(store, conversation, "wildcard-free question")

    assert conversation not in [c.id for c in store.list_conversations(search=search, limit=100)[0]]


def test_search_cant_inject_sql(store: ConversationStore, conversation: UUID) -> None:
    save(store, conversation, "Safe question")

    found, _ = store.list_conversations(search="'; DROP TABLE querymind_app.turns; --")

    assert found == []
    assert store.get(conversation).turns == 1


def test_recent_turns_are_the_last_ones_oldest_first(
    store: ConversationStore, conversation: UUID
) -> None:
    for number in range(1, 5):
        save(store, conversation, f"Question {number}")

    recent = store.recent_turns(conversation, limit=2)

    assert [turn.n for turn in recent] == [3, 4]
    assert store.recent_turns(uuid4(), limit=2) == []


def test_delete_removes_the_turns_too(
    store: ConversationStore, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, conversation)

    store.delete(conversation)

    with pytest.raises(ConversationNotFoundError):
        store.get(conversation)
    row = app_conn.execute(
        f"SELECT count(*) FROM {SCHEMA}.turns WHERE conversation_id = %s",
        (conversation,),
    ).fetchone()
    assert row == (0,)


@pytest.mark.parametrize(
    "action",
    [
        lambda store, missing: store.get(missing),
        lambda store, missing: store.update(missing, pinned=True),
        lambda store, missing: store.delete(missing),
    ],
)
def test_a_missing_conversation_is_said_clearly(store: ConversationStore, action: Any) -> None:
    with pytest.raises(ConversationNotFoundError):
        action(store, uuid4())
