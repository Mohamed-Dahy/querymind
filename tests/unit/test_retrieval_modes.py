"""When does the agent retrieve (3.3)? SCHEMA_RETRIEVAL auto / on / off, the graph node, and the
fallbacks a person must be able to see. Fake LLM, stubbed executor and search: no DB, no network.
"""

import logging
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import SecretStr

from querymind.agent import graph as graph_module
from querymind.agent import pipeline as pipeline_module
from querymind.agent.graph import build_graph
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.agent.state import AgentContext, new_turn
from querymind.config import ConfigError, Settings
from querymind.db.executor import QueryResult
from querymind.llm.embeddings import FakeEmbeddings
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.sql_validator import ValidatedQuery
from querymind.semantic import retrieval as retrieval_module
from querymind.semantic.index import IndexHit, IndexStatus, SchemaIndexError
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever
from tests.unit.retrieval_fixtures import FOOD, MUSIC, SCHEMA

RESULT = QueryResult(columns=["n"], rows=[[3]], row_count=1, truncated=False, duration_ms=1)
SHIPPERS = SqlDraft(sql="SELECT count(*) AS n FROM shippers", explanation="Counts.", tables_used=[])


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "llm_provider": "groq",
        "groq_api_key": SecretStr("gsk_test"),
        "groq_model": "openai/gpt-oss-120b",
        "semantic_layer_paths": [],
        "retrieval_min_tables": 5,  # the 8-table fixture schema counts as "big"
        "retrieval_top_k": 1,
    }
    return Settings.model_validate(values | overrides)


@pytest.fixture
def stubs(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """No DB: fixed schema, stubbed executor, a usable index whose search finds `shippers`."""
    monkeypatch.setattr(pipeline_module, "introspect_schema", lambda conn: SCHEMA)
    monkeypatch.setattr(graph_module, "execute_query", lambda conn, query, **_: RESULT)
    status = MagicMock(return_value=IndexStatus(usable=True))
    monkeypatch.setattr(pipeline_module, "index_status", status)
    hits = [IndexHit(id="table:shippers", kind="table", table="shippers", similarity=0.9)]
    monkeypatch.setattr(retrieval_module, "search", lambda conn, vector, **_: hits)
    return status


def ask(pipeline: Pipeline) -> AskResult:
    reply = pipeline.ask("How many shippers?", summarize_answer=False)
    assert isinstance(reply, AskResult)
    return reply


def test_auto_retrieves_on_a_big_schema(stubs: MagicMock) -> None:
    llm = FakeLLMProvider([SHIPPERS])
    ask(Pipeline(llm=llm, settings=settings(), connect=MagicMock(), embeddings=FakeEmbeddings()))
    prompt = llm.calls[0].prompt
    assert "Table shippers\n" in prompt and "Table customer\n" not in prompt


def test_auto_sends_a_small_schema_whole(stubs: MagicMock) -> None:
    llm = FakeLLMProvider([SHIPPERS])
    small = settings(retrieval_min_tables=8)  # 8 tables is not more than 8
    ask(Pipeline(llm=llm, settings=small, connect=MagicMock(), embeddings=FakeEmbeddings()))
    assert "Table customer\n" in llm.calls[0].prompt
    stubs.assert_not_called()  # the index isn't even checked


def test_off_always_sends_the_whole_schema(stubs: MagicMock) -> None:
    llm = FakeLLMProvider([SHIPPERS])
    off = settings(schema_retrieval="off")
    ask(Pipeline(llm=llm, settings=off, connect=MagicMock(), embeddings=FakeEmbeddings()))
    assert "Table customer\n" in llm.calls[0].prompt


def test_auto_without_embeddings_warns_and_sends_everything(
    stubs: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    llm = FakeLLMProvider([SHIPPERS])
    with caplog.at_level(logging.WARNING):
        ask(Pipeline(llm=llm, settings=settings(), connect=MagicMock()))
    assert "Table customer\n" in llm.calls[0].prompt
    assert "GOOGLE_API_KEY" in caplog.text


def test_on_without_embeddings_fails_at_startup() -> None:
    with pytest.raises(ConfigError, match="GOOGLE_API_KEY"):
        Pipeline(
            llm=FakeLLMProvider(), settings=settings(schema_retrieval="on"), connect=MagicMock()
        )


def test_an_unusable_index_is_a_warning_in_auto_and_an_error_in_on(
    stubs: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    stubs.return_value = IndexStatus(usable=False, message="there is no schema index yet")
    llm = FakeLLMProvider([SHIPPERS])
    with caplog.at_level(logging.WARNING):
        ask(
            Pipeline(llm=llm, settings=settings(), connect=MagicMock(), embeddings=FakeEmbeddings())
        )
    (skipped,) = [r for r in caplog.records if r.getMessage() == "schema retrieval skipped"]
    assert "no schema index" in skipped.reason  # type: ignore[attr-defined]
    assert "Table customer\n" in llm.calls[0].prompt

    on = Pipeline(
        llm=FakeLLMProvider(),
        settings=settings(schema_retrieval="on"),
        connect=MagicMock(),
        embeddings=FakeEmbeddings(),
    )
    with pytest.raises(SchemaIndexError, match="no schema index"):
        on.ask("How many shippers?")


def test_the_audit_says_what_the_llm_was_shown(
    stubs: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline = Pipeline(
        llm=FakeLLMProvider([SHIPPERS]),
        settings=settings(),
        connect=MagicMock(),
        embeddings=FakeEmbeddings(),
    )
    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        ask(pipeline)
    (audit,) = [r for r in caplog.records if r.name == "querymind.audit"]
    assert audit.schema_mode == "retrieved"  # type: ignore[attr-defined]
    assert audit.schema_tables == ["shippers"]  # type: ignore[attr-defined]
    assert audit.schema_tokens > 0  # type: ignore[attr-defined]
    assert audit.schema_fallback is None  # type: ignore[attr-defined]


# --- The graph node -------------------------------------------------------------------------


class StubExecutor:
    def __init__(self) -> None:
        self.queries: list[ValidatedQuery] = []

    def __call__(self, conn: psycopg.Connection, query: ValidatedQuery, **_: Any) -> QueryResult:
        self.queries.append(query)
        return RESULT


def test_retrieval_runs_first_and_repairs_see_a_wider_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(graph_module, "execute_query", StubExecutor())
    hits = [IndexHit(id="table:orders", kind="table", table="orders", similarity=0.9)]
    monkeypatch.setattr(retrieval_module, "search", lambda conn, vector, **_: hits)
    retriever = SchemaRetriever(
        catalog=SchemaCatalog(SCHEMA, [FOOD, MUSIC]),
        embeddings=FakeEmbeddings(),
        connect=MagicMock(),
        settings=settings(),
    )
    bad = SqlDraft(sql="DELETE FROM orders", explanation="", tables_used=[])  # → one repair
    llm = FakeLLMProvider([bad, SHIPPERS])
    context = AgentContext(
        llm=llm,
        settings=settings(),
        connect=MagicMock(),
        summarize_answer=False,
        retriever=retriever,
    )
    start = new_turn(
        question="Orders per shipper", schema_text="FULL", table_names=sorted(SCHEMA.table_names)
    )
    steps = [
        next(iter(u)) for u in build_graph().stream(start, context=context, stream_mode="updates")
    ]

    assert steps[:2] == ["retrieve_schema", "generate_sql"]
    first, repair = llm.calls
    assert "Table orders\n" in first.prompt and "Table shippers\n" not in first.prompt
    assert "Table shippers\n" in repair.prompt  # one join away: the repair can use it
    assert "FULL" not in first.prompt


def test_without_a_retriever_the_node_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph_module, "execute_query", StubExecutor())
    llm = FakeLLMProvider([SHIPPERS])
    context = AgentContext(
        llm=llm, settings=settings(), connect=MagicMock(), summarize_answer=False
    )
    start = new_turn(
        question="q", schema_text="Table shippers\n  Columns: n", table_names=["shippers"]
    )
    final = build_graph().invoke(start, context=context)
    assert "Table shippers\n  Columns: n" in llm.calls[0].prompt
    assert final["schema_selection"].mode == "full"
    assert final["schema_selection"].tables == ("shippers",)
