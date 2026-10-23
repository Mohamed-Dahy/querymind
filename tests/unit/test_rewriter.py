"""Query rewriting (3.6): the rewriter, and how retrieve_schema uses it. Fake LLM, no network.

What matters: no history → no LLM call; with QUERY_REWRITING on, both the typed follow-up and
its rewrite are searched; a failed rewrite still searches the typed one; the SQL prompt always
gets the original question.
"""

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from querymind.agent import graph as graph_module
from querymind.agent.graph import build_graph
from querymind.agent.rewriter import rewrite_question
from querymind.agent.schemas import SqlDraft, StandaloneQuestion, Turn
from querymind.agent.state import AgentContext, new_turn
from querymind.config import Settings
from querymind.db.executor import QueryResult
from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.retrieval import SchemaSelection, Selection

TOP3 = Turn(
    question="Who are the top 3 music store customers by spending?",
    kind="answer",
    sql="SELECT c.first_name, sum(i.total) FROM customer AS c JOIN invoice AS i ON true",
    columns=["first_name", "sum"],
    row_count=3,
)
REWRITTEN = StandaloneQuestion(question="Who looks after the top 3 music store customers?")
DRAFT = SqlDraft(sql="SELECT 1 AS n", explanation="One.", tables_used=[])
RESULT = QueryResult(columns=["n"], rows=[[1]], row_count=1, truncated=False, duration_ms=1)


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
        "query_rewriting": True,
    }
    return Settings.model_validate(values | overrides)


# --- The rewriter ------------------------------------------------------------------------------


def test_without_history_nothing_is_asked() -> None:
    llm = FakeLLMProvider()
    assert rewrite_question(llm, question="How many tracks?", history=[]) == "How many tracks?"
    assert llm.calls == []


def test_the_rewrite_sees_the_earlier_question_and_its_sql() -> None:
    llm = FakeLLMProvider([REWRITTEN])
    out = rewrite_question(llm, question="Who looks after them?", history=[TOP3])
    assert out == REWRITTEN.question
    (call,) = llm.calls
    assert "Question: Who are the top 3 music store customers by spending?" in call.prompt
    assert f"SQL: {TOP3.sql}" in call.prompt
    assert "<question>\nWho looks after them?\n</question>" in call.prompt
    assert call.system is not None and "standalone question" in call.system


def test_an_empty_rewrite_keeps_the_question() -> None:
    llm = FakeLLMProvider([StandaloneQuestion(question="   ")])
    assert rewrite_question(llm, question="And them?", history=[TOP3]) == "And them?"


def test_llm_errors_pass_through() -> None:
    llm = FakeLLMProvider([LLMProviderError("429")])
    with pytest.raises(LLMProviderError):
        rewrite_question(llm, question="And them?", history=[TOP3])


# --- In the graph --------------------------------------------------------------------------------


class RecordingRetriever:
    """Stands in for SchemaRetriever: records what it was asked to search."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Sequence[str]]] = []

    def select(
        self, question: str, *, history: Sequence[Turn] = (), also: Sequence[str] = ()
    ) -> Selection:
        self.calls.append((question, tuple(also)))
        info = SchemaSelection(mode="retrieved", tables=("customer",), schema_tokens=10)
        return Selection(
            schema_text="Table customer", repair_schema_text="Table customer", info=info
        )


def run_turn(
    monkeypatch: pytest.MonkeyPatch, llm: FakeLLMProvider, *, history: list[Turn], **overrides: Any
) -> tuple[RecordingRetriever, dict[str, Any]]:
    monkeypatch.setattr(graph_module, "execute_query", lambda conn, query, **_: RESULT)
    retriever = RecordingRetriever()
    context = AgentContext(
        llm=llm,
        settings=settings(**overrides),
        connect=MagicMock(),
        summarize_answer=False,
        retriever=retriever,  # type: ignore[arg-type]
    )
    start = new_turn(question="Who looks after them?", schema_text="FULL", table_names=["customer"])
    start["history"] = history
    final = build_graph().invoke(start, context=context)
    return retriever, final


def test_a_follow_up_is_searched_as_typed_and_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLMProvider([REWRITTEN, DRAFT])
    retriever, final = run_turn(monkeypatch, llm, history=[TOP3])
    assert retriever.calls == [("Who looks after them?", (REWRITTEN.question,))]
    assert final["search_question"] == REWRITTEN.question
    sql_call = llm.calls[1]
    assert (
        "<question>\nWho looks after them?\n</question>" in sql_call.prompt
    )  # original, as in 2.6


def test_a_first_question_is_never_rewritten(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLMProvider([DRAFT])  # only the SQL call
    retriever, final = run_turn(monkeypatch, llm, history=[])
    assert retriever.calls == [("Who looks after them?", ())]
    assert final["search_question"] is None


def test_off_by_default_means_no_rewrite_call(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.model_fields["query_rewriting"].default is False
    llm = FakeLLMProvider([DRAFT])
    retriever, _ = run_turn(monkeypatch, llm, history=[TOP3], query_rewriting=False)
    assert retriever.calls == [("Who looks after them?", ())]
    assert len(llm.calls) == 1


@pytest.mark.parametrize("error", [LLMProviderError("429"), LLMOutputError("not a question")])
def test_a_failed_rewrite_searches_the_question_as_typed(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    llm = FakeLLMProvider([error, DRAFT])
    retriever, final = run_turn(monkeypatch, llm, history=[TOP3])
    assert retriever.calls == [("Who looks after them?", ())]
    assert final["search_question"] is None
    assert final["result"] == RESULT  # the turn still answers
