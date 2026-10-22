"""The agent graph's wiring with a fake LLM and a stubbed executor: no DB, no network.

The real DB + graph path is covered by tests/integration/test_pipeline.py.
"""

from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import BaseModel, SecretStr

from querymind.agent import graph as graph_module
from querymind.agent.graph import build_graph
from querymind.agent.schemas import AnswerSummary, SqlDraft
from querymind.agent.state import AgentContext, AgentState
from querymind.config import Settings
from querymind.db.executor import QueryResult
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import ValidatedQuery

RESULT = QueryResult(columns=["n"], rows=[[91]], row_count=1, truncated=False, duration_ms=2)
START: AgentState = {
    "question": "How many customers are there?",
    "schema_text": "Table customers (customer_id text PK, ...)",
    "table_names": ["customers", "orders"],
}


def settings() -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "groq_api_key": SecretStr("gsk_test"),
        "max_rows": 1000,
    }
    return Settings.model_validate(values)


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[ValidatedQuery]:
    """Stub the executor: record each query it gets and return RESULT."""
    queries: list[ValidatedQuery] = []

    def fake_execute(
        conn: psycopg.Connection, query: ValidatedQuery, *, timeout_seconds: int
    ) -> QueryResult:
        queries.append(query)
        return RESULT

    monkeypatch.setattr(graph_module, "execute_query", fake_execute)
    return queries


def context(llm: FakeLLMProvider, connect: MagicMock, **options: Any) -> AgentContext:
    return AgentContext(llm=llm, settings=settings(), connect=connect, **options)


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Counts the customers.", tables_used=["customers"])


def answering_llm() -> FakeLLMProvider:
    """A fake LLM that writes a count query, then summarizes it."""
    return FakeLLMProvider([draft("SELECT count(*) AS n FROM customers"), "There are 91."])


def test_nodes_run_in_order_and_fill_the_state(executed: list[ValidatedQuery]) -> None:
    ctx = context(answering_llm(), MagicMock())
    steps = [
        next(iter(update))  # "updates" mode yields {node name: fields it returned}
        for update in build_graph().stream(START, context=ctx, stream_mode="updates")
    ]
    assert steps == ["generate_sql", "run_sql", "summarize"]

    final = build_graph().invoke(START, context=context(answering_llm(), MagicMock()))
    assert final["sql"] == "SELECT COUNT(*) AS n FROM customers LIMIT 1001"  # the validated SQL
    assert final["explanation"] == "Counts the customers."
    assert final["result"] == RESULT
    assert final["answer"] == AnswerSummary(text="There are 91.", from_llm=True)


def test_only_validated_sql_reaches_the_executor(executed: list[ValidatedQuery]) -> None:
    build_graph().invoke(START, context=context(answering_llm(), MagicMock()))
    (query,) = executed
    assert isinstance(query, ValidatedQuery)
    assert query.sql.endswith("LIMIT 1001")


def test_unsafe_sql_stops_before_any_connection(executed: list[ValidatedQuery]) -> None:
    llm = FakeLLMProvider([draft("DELETE FROM customers")])
    connect = MagicMock()
    with pytest.raises(UnsafeSqlError, match="Only SELECT"):
        build_graph().invoke(START, context=context(llm, connect))
    connect.assert_not_called()  # validation comes first: no connection is even opened
    assert executed == []
    assert len(llm.calls) == 1  # and no summary call


def test_the_connection_is_closed_after_running(executed: list[ValidatedQuery]) -> None:
    connect = MagicMock()
    build_graph().invoke(START, context=context(answering_llm(), connect))
    connect.assert_called_once()
    connect.return_value.__exit__.assert_called_once()  # the `with` block closed it


def test_summary_can_be_skipped(executed: list[ValidatedQuery]) -> None:
    llm = FakeLLMProvider([draft("SELECT count(*) AS n FROM customers")])
    final = build_graph().invoke(START, context=context(llm, MagicMock(), summarize_answer=False))
    assert final["answer"] == AnswerSummary(text="n: 91", from_llm=False)
    assert len(llm.calls) == 1  # only the SQL call


def test_state_holds_only_saveable_values(executed: list[ValidatedQuery]) -> None:
    # Phase 2.6 saves the state between turns. A connection can't be saved, and a reloaded
    # ValidatedQuery would carry the validator's stamp without having been validated.
    final = build_graph().invoke(START, context=context(answering_llm(), MagicMock()))
    for key, value in final.items():
        assert isinstance(value, str | list | BaseModel), key
        assert not isinstance(value, ValidatedQuery), key


def test_diagram_shows_the_nodes_and_edges() -> None:
    mermaid = build_graph().get_graph().draw_mermaid()
    for edge in [
        "__start__ --> generate_sql",
        "generate_sql --> run_sql",
        "run_sql --> summarize",
        "summarize --> __end__",
    ]:
        assert edge in mermaid
