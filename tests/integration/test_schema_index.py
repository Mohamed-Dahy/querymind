"""The schema index on the real Postgres + pgvector (3.3), with fake embeddings (no network).

Uses its own table, querymind.test_schema_docs, dropped afterwards: the real index
(querymind.schema_docs, built by scripts/build_index.py) is never touched by tests.
"""

from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.schema import SchemaInfo, introspect_schema
from querymind.llm.embeddings import FakeEmbeddings
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.dictionary import load_dictionaries
from querymind.semantic.documents import SchemaDoc, build_documents
from querymind.semantic.index import (
    INDEX_SCHEMA,
    ensure_index,
    index_status,
    search,
    upsert_documents,
)
from querymind.semantic.loader import load_semantic_layers
from querymind.semantic.render import schema_prompt_text

pytestmark = pytest.mark.integration

TEST_TABLE = "test_schema_docs"
QUALIFIED = sql.Identifier(INDEX_SCHEMA, TEST_TABLE)


@pytest.fixture(scope="module")
def schema(settings: Settings) -> SchemaInfo:
    with connect_readonly(settings) as conn:
        return introspect_schema(conn)


@pytest.fixture(scope="module")
def docs(settings: Settings, schema: SchemaInfo) -> list[SchemaDoc]:
    layers = load_semantic_layers(settings.semantic_layer_paths)
    return build_documents(schema, layers, load_dictionaries(settings.semantic_layer_paths))


@pytest.fixture
def index(settings: Settings, docs: list[SchemaDoc]) -> Iterator[FakeEmbeddings]:
    """A fresh test index holding every document; dropped after the test."""
    embeddings = FakeEmbeddings()
    with psycopg.connect(settings.admin_conninfo()) as conn:
        conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(QUALIFIED))
        ensure_index(conn, reader=settings.analyst_ro_user, table=TEST_TABLE)
        upsert_documents(conn, docs, embeddings, table=TEST_TABLE)
    try:
        yield embeddings
    finally:
        with psycopg.connect(settings.admin_conninfo()) as conn:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(QUALIFIED))


def test_every_table_and_metric_is_indexed(
    settings: Settings, index: FakeEmbeddings, docs: list[SchemaDoc]
) -> None:
    with connect_readonly(settings) as conn:  # the read-only role may search it
        count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(QUALIFIED)).fetchone()
    assert count == (len(docs),)
    assert sum(d.kind == "table" for d in docs) == 25


def test_only_changed_documents_are_embedded_again(
    settings: Settings, index: FakeEmbeddings, docs: list[SchemaDoc]
) -> None:
    edited = [
        d.model_copy(update={"text": d.text + " Edited."}) if d.name == "track" else d for d in docs
    ]
    with psycopg.connect(settings.admin_conninfo()) as conn:
        assert upsert_documents(conn, docs, index, table=TEST_TABLE) == (0, 0)  # nothing changed
        assert upsert_documents(conn, edited, index, table=TEST_TABLE) == (1, 0)
        assert upsert_documents(conn, edited[1:], index, table=TEST_TABLE) == (0, 1)  # one gone


def test_search_ranks_the_matching_table_first(settings: Settings, index: FakeEmbeddings) -> None:
    with connect_readonly(settings) as conn:
        hits = search(
            conn, index.embed_query("invoice_line unit_price quantity"), limit=3, table=TEST_TABLE
        )
    assert hits[0].table == "invoice_line"
    assert hits[0].similarity >= hits[1].similarity >= hits[2].similarity


def test_the_read_only_role_cannot_change_the_index(
    settings: Settings, index: FakeEmbeddings
) -> None:
    with connect_readonly(settings) as conn, pytest.raises(psycopg.Error):
        conn.execute(sql.SQL("DELETE FROM {}").format(QUALIFIED))


def test_llm_sql_can_never_read_the_index(
    settings: Settings, schema: SchemaInfo, index: FakeEmbeddings
) -> None:
    # The validator only allows public tables: the index is invisible to generated SQL.
    for attempt in (
        f"SELECT text FROM {INDEX_SCHEMA}.{TEST_TABLE}",
        f"SELECT text FROM {TEST_TABLE}",
    ):
        with pytest.raises(UnsafeSqlError, match="not allowed"):
            validate_sql(attempt, allowed_tables=schema.table_names, max_rows=10)
    assert TEST_TABLE not in schema.table_names  # introspection lists public tables only


def test_index_status(settings: Settings, index: FakeEmbeddings, docs: list[SchemaDoc]) -> None:
    with connect_readonly(settings) as conn:
        assert index_status(conn, docs, model="fake", table=TEST_TABLE).usable
    with connect_readonly(settings) as conn:
        wrong = index_status(conn, docs, model="gemini-embedding-001", table=TEST_TABLE)
    assert not wrong.usable and "embedded with fake" in wrong.message
    with connect_readonly(settings) as conn:
        stale = index_status(conn, docs[1:], model="fake", table=TEST_TABLE)
    assert stale.usable and "1 schema documents changed" in stale.message
    with connect_readonly(settings) as conn:
        missing = index_status(conn, docs, model="fake", table="no_such_index")
    assert not missing.usable and "no schema index" in missing.message


def test_a_question_sees_only_the_retrieved_tables(
    settings: Settings, schema: SchemaInfo, index: FakeEmbeddings
) -> None:
    sql_draft = SqlDraft(
        sql="SELECT count(*) AS n FROM track", explanation="Counts.", tables_used=["track"]
    )
    llm = FakeLLMProvider([sql_draft])
    on = settings.model_copy(update={"schema_retrieval": "on"})
    pipeline = Pipeline(llm=llm, settings=on, embeddings=index, index_table=TEST_TABLE)
    reply = pipeline.ask("How many track rows are in the track table?", summarize_answer=False)

    assert isinstance(reply, AskResult) and reply.result.rows == [[3503]]
    prompt = llm.calls[0].prompt
    assert "Table track\n" in prompt
    assert "Table orders\n" not in prompt  # Northwind wasn't needed, so it wasn't sent
    full = schema_prompt_text(schema, load_semantic_layers(settings.semantic_layer_paths))
    assert len(prompt) < len(full) / 2  # the point of 3.3
