"""The app's storage on the real database: who can reach what, and that saving really works.

Needs the setup too: `uv run python data/setup_app_db.py`, with APP_DB_USER and APP_DB_PASSWORD set.
Skipped without them, because history is optional. CI sets them, so CI always runs these.
"""

import random
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
from querymind.store.conversations import ConversationStore, Owner, SavedQuestion, SavedTurn
from querymind.store.errors import (
    ConversationNotFoundError,
    StoreUnavailableError,
    TurnNotFoundError,
)

pytestmark = pytest.mark.integration

APP_TABLES = (
    "conversations",
    "turns",
    "saved_questions",
    # 5.1: the agent must never read password hashes or refresh tokens.
    "users",
    "workspaces",
    "memberships",
    "refresh_tokens",
    "audit_log",  # 5.5
    "secrets",  # 5.7: encrypted, but the agent must not even see the tokens
    "llm_keys",  # 5.8
    "connections",  # 5.9
)
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
def conversation(store: ConversationStore, owner: Owner) -> Iterator[UUID]:
    """A new conversation id, deleted again after the test."""
    conversation_id = uuid4()
    yield conversation_id
    with suppress(ConversationNotFoundError):  # the test never saved one, or deleted it itself
        store.delete(conversation_id, owner=owner)


@pytest.fixture
def app_conn(settings: Settings, store: ConversationStore) -> Iterator[psycopg.Connection]:
    """Connection as the app's role. Closing without commit undoes anything a test did."""
    conn = connect_app(settings)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def saved_question(store: ConversationStore, owner: Owner) -> Iterator[SavedQuestion]:
    """A saved question with words no other test uses, removed again after the test."""
    saved = store.add_saved_question(f"Sales this week? {uuid4()}", owner=owner)
    yield saved
    store.remove_saved_question(saved.id, owner=owner)


def save(
    store: ConversationStore, owner: Owner, conversation_id: UUID, question: str = "Top customers?"
) -> int:
    return store.save_turn(
        conversation_id,
        question=question,
        kind="answer",
        sql="SELECT company_name FROM customers",
        reply=REPLY,
        trace=[{"type": "step", "node": "generate_sql", "status": "started"}],
        owner=owner,
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
    settings: Settings, store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation)
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
    store: ConversationStore, owner: Owner, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, owner, conversation)

    row = app_conn.execute(
        f"SELECT reply::text, trace::text FROM {SCHEMA}.turns WHERE conversation_id = %s",
        (conversation,),
    ).fetchone()

    assert row is not None
    assert "Alfreds Futterkiste" not in row[0] + row[1]
    assert "rows" not in store.get(conversation, owner=owner).saved_turns[0].reply
    assert store.get(conversation, owner=owner).saved_turns[0].reply["row_count"] == 2


def test_the_database_itself_refuses_rows(
    store: ConversationStore, owner: Owner, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, owner, conversation)

    with pytest.raises(errors.CheckViolation):
        app_conn.execute(
            f"""INSERT INTO {SCHEMA}.turns (conversation_id, n, question, kind, reply)
                VALUES (%s, 99, 'q', 'answer', '{{"rows": [[1]]}}')""",
            (conversation,),
        )


# --- Saving and reading -------------------------------------------------------------------------


def test_turns_are_numbered_and_the_first_question_is_the_title(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    assert save(store, owner, conversation, "Top customers?") == 1
    assert save(store, owner, conversation, "Now only for 1997") == 2

    saved = store.get(conversation, owner=owner)

    assert saved.title == "Top customers?"
    assert saved.turns == 2
    assert [turn.question for turn in saved.saved_turns] == ["Top customers?", "Now only for 1997"]
    first: SavedTurn = saved.saved_turns[0]
    assert first.sql == "SELECT company_name FROM customers"
    assert first.trace[0]["node"] == "generate_sql"


def test_saves_at_the_same_time_get_different_numbers(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        numbers = list(pool.map(lambda _: save(store, owner, conversation), range(8)))

    assert sorted(numbers) == [1, 2, 3, 4, 5, 6, 7, 8]


def test_a_new_turn_moves_the_conversation_up_but_renaming_doesnt(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    other = uuid4()
    try:
        save(store, owner, conversation)
        save(store, owner, other)
        store.update(conversation, title="Renamed", owner=owner)
        assert store.list_conversations(limit=2, owner=owner)[0][0].id == other

        save(store, owner, conversation)
        assert store.list_conversations(limit=2, owner=owner)[0][0].id == conversation
    finally:
        store.delete(other, owner=owner)


def test_pinned_comes_first(store: ConversationStore, owner: Owner, conversation: UUID) -> None:
    other = uuid4()
    try:
        save(store, owner, conversation)
        save(store, owner, other)
        store.update(conversation, pinned=True, owner=owner)

        found, _ = store.list_conversations(limit=1, owner=owner)

        assert found[0].id == conversation and found[0].pinned
    finally:
        store.delete(other, owner=owner)


def test_paging_says_when_there_is_more(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    other = uuid4()
    try:
        save(store, owner, conversation, "paging-marker one")
        save(store, owner, other, "paging-marker two")

        first, more = store.list_conversations(search="paging-marker", limit=1, owner=owner)
        second, last = store.list_conversations(
            search="paging-marker", limit=1, offset=1, owner=owner
        )

        assert len(first) == 1 and more
        assert len(second) == 1 and not last
        assert first[0].id != second[0].id
    finally:
        store.delete(other, owner=owner)


def test_search_looks_in_titles_and_in_every_question(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation, "First question")
    save(store, owner, conversation, "Something about ZEBRA sales")

    assert [c.id for c in store.list_conversations(search="zebra", owner=owner)[0]] == [
        conversation
    ]
    store.update(conversation, title="QUOKKA report", owner=owner)
    assert [c.id for c in store.list_conversations(search="quokka", owner=owner)[0]] == [
        conversation
    ]


@pytest.mark.parametrize("search", ["%", "_", "\\", "100%_\\"])
def test_search_treats_wildcards_as_plain_text(
    store: ConversationStore, owner: Owner, conversation: UUID, search: str
) -> None:
    save(store, owner, conversation, "wildcard-free question")

    assert conversation not in [
        c.id for c in store.list_conversations(search=search, limit=100, owner=owner)[0]
    ]


def test_search_cant_inject_sql(store: ConversationStore, owner: Owner, conversation: UUID) -> None:
    save(store, owner, conversation, "Safe question")

    found, _ = store.list_conversations(search="'; DROP TABLE querymind_app.turns; --", owner=owner)

    assert found == []
    assert store.get(conversation, owner=owner).turns == 1


def test_get_turn_finds_one_turn_or_says_which_is_missing(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation, "First")
    save(store, owner, conversation, "Second")

    assert store.get_turn(conversation, 2, owner=owner).question == "Second"
    with pytest.raises(TurnNotFoundError):
        store.get_turn(conversation, 3, owner=owner)
    with pytest.raises(ConversationNotFoundError):
        store.get_turn(uuid4(), 1, owner=owner)


def test_recent_turns_are_the_last_ones_oldest_first(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    for number in range(1, 5):
        save(store, owner, conversation, f"Question {number}")

    recent = store.recent_turns(conversation, limit=2)

    assert [turn.n for turn in recent] == [3, 4]
    assert store.recent_turns(uuid4(), limit=2) == []


def test_delete_removes_the_turns_too(
    store: ConversationStore, owner: Owner, conversation: UUID, app_conn: psycopg.Connection
) -> None:
    save(store, owner, conversation)

    store.delete(conversation, owner=owner)

    with pytest.raises(ConversationNotFoundError):
        store.get(conversation, owner=owner)
    row = app_conn.execute(
        f"SELECT count(*) FROM {SCHEMA}.turns WHERE conversation_id = %s",
        (conversation,),
    ).fetchone()
    assert row == (0,)


ACTIONS = [
    lambda store, owner, missing: store.get(missing, owner=owner),
    lambda store, owner, missing: store.get_turn(missing, 1, owner=owner),
    lambda store, owner, missing: store.update(missing, pinned=True, owner=owner),
    lambda store, owner, missing: store.delete(missing, owner=owner),
    lambda store, owner, missing: store.check_owner(missing, owner=owner),
]


@pytest.mark.parametrize("action", ACTIONS[:4])
def test_a_missing_conversation_is_said_clearly(
    store: ConversationStore, owner: Owner, action: Any
) -> None:
    with pytest.raises(ConversationNotFoundError):
        action(store, owner, uuid4())


# --- Whose rows (5.1c) --------------------------------------------------------------------------


@pytest.mark.parametrize("action", ACTIONS)
def test_someone_elses_conversation_reads_as_missing(
    store: ConversationStore, owner: Owner, other_owner: Owner, conversation: UUID, action: Any
) -> None:
    # The same 404 as a wrong id: nobody can find out which ids exist.
    save(store, owner, conversation)

    with pytest.raises(ConversationNotFoundError):
        action(store, other_owner, conversation)
    assert store.get(conversation, owner=owner).saved_turns  # still there, untouched


def test_a_turn_cant_be_added_to_someone_elses_conversation(
    store: ConversationStore, owner: Owner, other_owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation)

    with pytest.raises(ConversationNotFoundError):
        save(store, other_owner, conversation, "Sneaky follow-up")
    assert len(store.get(conversation, owner=owner).saved_turns) == 1


def test_lists_and_saved_questions_are_per_user(
    store: ConversationStore, owner: Owner, other_owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation)
    mine = store.add_saved_question(f"Mine? {uuid4()}", owner=owner)

    found, _ = store.list_conversations(owner=other_owner, limit=100)
    assert conversation not in {c.id for c in found}
    assert mine.id not in {q.id for q in store.list_saved_questions(owner=other_owner)}
    store.remove_saved_question(mine.id, owner=other_owner)  # not theirs: nothing happens
    assert mine.id in {q.id for q in store.list_saved_questions(owner=owner)}
    # The same words, saved by someone else, are their own row.
    theirs = store.add_saved_question(mine.question, owner=other_owner)
    assert theirs.id != mine.id


def test_an_owner_has_to_be_a_real_account(store: ConversationStore, conversation: UUID) -> None:
    ghost = Owner(user_id=uuid4(), workspace_id=uuid4())

    with pytest.raises(StoreUnavailableError):  # the foreign keys refuse it
        save(store, ghost, conversation)


# --- Saved questions ----------------------------------------------------------------------------


def test_a_saved_question_is_listed(
    store: ConversationStore, owner: Owner, saved_question: SavedQuestion
) -> None:
    assert saved_question in store.list_saved_questions(owner=owner)


def test_saving_the_same_words_twice_keeps_one_row(
    store: ConversationStore, owner: Owner, saved_question: SavedQuestion
) -> None:
    again = store.add_saved_question(saved_question.question, owner=owner)

    assert again == saved_question
    same = [
        one
        for one in store.list_saved_questions(owner=owner)
        if one.question == saved_question.question
    ]
    assert len(same) == 1


def test_the_newest_saved_question_comes_first(
    store: ConversationStore, owner: Owner, saved_question: SavedQuestion
) -> None:
    newer = store.add_saved_question(f"Newer {uuid4()}", owner=owner)
    try:
        ids = [one.id for one in store.list_saved_questions(owner=owner)]
        assert ids.index(newer.id) < ids.index(saved_question.id)
    finally:
        store.remove_saved_question(newer.id, owner=owner)


def test_removing_a_saved_question_twice_is_fine(
    store: ConversationStore, owner: Owner, saved_question: SavedQuestion
) -> None:
    store.remove_saved_question(saved_question.id, owner=owner)
    store.remove_saved_question(saved_question.id, owner=owner)

    assert saved_question not in store.list_saved_questions(owner=owner)


def test_quotes_in_a_saved_question_stay_plain_text(store: ConversationStore, owner: Owner) -> None:
    words = f"'; DROP TABLE querymind_app.saved_questions; -- {uuid4()}"

    saved = store.add_saved_question(words, owner=owner)
    try:
        assert saved.question == words
        assert saved in store.list_saved_questions(owner=owner)
    finally:
        store.remove_saved_question(saved.id, owner=owner)


def test_the_longest_question_the_api_takes_can_be_saved(
    store: ConversationStore, owner: Owner
) -> None:
    """4,000 bytes that don't compress: more than a plain unique index takes (2,704)."""
    picks = random.Random(42)
    words = "".join(chr(picks.randrange(0x20000, 0x2A6DF)) for _ in range(1000))

    saved = store.add_saved_question(words, owner=owner)
    try:
        assert saved.question == words
    finally:
        store.remove_saved_question(saved.id, owner=owner)


# --- Recent questions per table -----------------------------------------------------------------


def save_using(
    store: ConversationStore, owner: Owner, conversation_id: UUID, question: str, tables: list[str]
) -> int:
    return store.save_turn(
        conversation_id,
        question=question,
        kind="answer",
        sql="SELECT 1",
        reply={**REPLY, "tables_used": tables},
        owner=owner,
    )


def test_recent_questions_find_the_tables_a_query_used(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    table = f"table_{uuid4().hex}"
    save_using(store, owner, conversation, "First?", [table, "customers"])
    n = save_using(store, owner, conversation, "Second?", [table])
    save_using(store, owner, conversation, "Other?", ["customers"])

    found = store.recent_questions_using(table, limit=5, owner=owner)

    assert [one.question for one in found] == ["Second?", "First?"]
    assert (found[0].conversation_id, found[0].n) == (conversation, n)


def test_recent_questions_keep_to_the_limit(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    table = f"table_{uuid4().hex}"
    for number in range(3):
        save_using(store, owner, conversation, f"Question {number}?", [table])

    assert len(store.recent_questions_using(table, limit=2, owner=owner)) == 2


def test_recent_questions_need_the_whole_name_and_treat_it_as_plain_text(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    table = f"customers_{uuid4().hex}"
    save_using(store, owner, conversation, "Top?", [table])

    assert store.recent_questions_using(table[:-1], limit=5, owner=owner) == []
    assert store.recent_questions_using("' OR 1=1 --", limit=5, owner=owner) == []


def test_a_turn_saved_before_tables_used_existed_is_simply_not_found(
    store: ConversationStore, owner: Owner, conversation: UUID
) -> None:
    save(store, owner, conversation)  # REPLY has no tables_used

    assert store.recent_questions_using("company_name", limit=5, owner=owner) == []
