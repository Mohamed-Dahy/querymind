"""Conversation memory (2.6): Pipeline.ask with a conversation id, a fake LLM, a stubbed
executor and a fixed schema. No DB, no network.

The dangerous bugs here are silent ones: a saved turn leaking into the next (an old result
"answering" a new question, old failures using up the new attempt budget), or rows reaching
the LLM through the history (§8.5).
"""

import warnings
from collections import deque
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import SecretStr

from querymind.agent import graph as graph_module
from querymind.agent import pipeline as pipeline_module
from querymind.agent.pipeline import Pipeline
from querymind.agent.prompts.sql_generation import build_user_prompt, format_history
from querymind.agent.schemas import AskResult, Clarification, SqlDraft, Turn
from querymind.agent.state import MAX_HISTORY_TURNS, AgentState, keep_last_turns, new_turn
from querymind.config import Settings
from querymind.db.errors import QueryExecutionError
from querymind.db.executor import QueryResult
from querymind.db.schema import Column, SchemaInfo, Table, describe_schema
from querymind.llm import LLMProviderError
from querymind.llm.fake import FakeLLMProvider, FakeResponse
from querymind.safety.sql_validator import ValidatedQuery

SCHEMA = SchemaInfo(
    tables=(
        Table(
            name="customers",
            columns=(Column(name="company_name", data_type="text"),),
        ),
        Table(name="orders", columns=(Column(name="order_date", data_type="date"),)),
    )
)
# Values that must never reach a later prompt: they are rows, not SQL.
TOP_CUSTOMERS = QueryResult(
    columns=["company_name", "revenue"],
    rows=[["Alfreds Futterkiste", 12345.67], ["Ernst Handel", 11111.11]],
    row_count=2,
    truncated=False,
    duration_ms=3,
)
ROUND_ERROR = "function round(double precision, integer) does not exist"


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Test query.", tables_used=["customers"])


TOP = draft("SELECT company_name, revenue FROM customers")
IN_1997 = draft("SELECT company_name, revenue FROM customers WHERE 1997 = 1997")
ASK_BACK = SqlDraft(
    clarification=Clarification(
        question="How would you define best?",
        options=["by total revenue", "by number of orders"],
    ),
    sql="",
    explanation="",
    tables_used=[],
)


def db_error() -> QueryExecutionError:
    message = f"The database could not run the query: {ROUND_ERROR}"
    return QueryExecutionError(message, detail=ROUND_ERROR)


class StubExecutor:
    """Stands in for execute_query: the next queued reply (a result or an error to raise)."""

    def __init__(self) -> None:
        self.queries: list[ValidatedQuery] = []
        self.replies: deque[QueryResult | Exception] = deque()

    def __call__(
        self, conn: psycopg.Connection, query: ValidatedQuery, *, timeout_seconds: int
    ) -> QueryResult:
        self.queries.append(query)
        reply = self.replies.popleft() if self.replies else TOP_CUSTOMERS
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(graph_module, "execute_query", stub)
    monkeypatch.setattr(pipeline_module, "introspect_schema", lambda conn: SCHEMA)
    return stub


def pipeline_with(*responses: FakeResponse) -> tuple[Pipeline, FakeLLMProvider]:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "groq_api_key": SecretStr("gsk_test"),
        "max_sql_attempts": 3,
    }
    llm = FakeLLMProvider(responses)
    return Pipeline(llm=llm, settings=Settings.model_validate(values), connect=MagicMock()), llm


def ask(pipeline: Pipeline, question: str, conversation_id: str | None = "c1") -> AskResult:
    reply = pipeline.ask(question, conversation_id=conversation_id, summarize_answer=False)
    assert isinstance(reply, AskResult), reply
    return reply


# --- Follow-ups ------------------------------------------------------------------------------


def test_a_follow_up_sees_the_earlier_sql_but_never_its_rows(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, IN_1997)
    first = ask(pipeline, "Top customers by revenue")
    ask(pipeline, "Only for 1997")

    follow_up = llm.calls[1].prompt
    assert "Question: Top customers by revenue" in follow_up
    assert f"SQL: {first.sql}" in follow_up  # the validated SQL, ready to build on
    assert "Result: 2 rows, columns: company_name, revenue" in follow_up
    for value in ("Alfreds Futterkiste", "Ernst Handel", "12345.67"):
        assert value not in follow_up  # rows never go back to the LLM (§8.5)


def test_a_standalone_question_gets_exactly_the_old_prompt(executor: StubExecutor) -> None:
    # No id: nothing is remembered. The prompt is the one the golden and clarify evals measured.
    pipeline, llm = pipeline_with(TOP, TOP)
    ask(pipeline, "Top customers by revenue", conversation_id=None)
    ask(pipeline, "Only for 1997", conversation_id=None)
    schema_text = describe_schema(SCHEMA)
    assert llm.calls[1].prompt == build_user_prompt(
        schema_text=schema_text, question="Only for 1997"
    )
    assert "<history>" not in llm.calls[1].prompt


def test_a_reply_to_a_clarification_sees_the_question_asked(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(ASK_BACK, TOP)
    pipeline.ask("Who are our best customers?", conversation_id="c1")
    ask(pipeline, "by total revenue")
    assert (
        "You asked back: How would you define best? Options: by total revenue; by number of "
        "orders" in llm.calls[1].prompt
    )


def test_conversations_are_kept_apart(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, TOP)
    ask(pipeline, "Top customers by revenue", conversation_id="alice")
    ask(pipeline, "How many orders?", conversation_id="bob")
    assert "<history>" not in llm.calls[1].prompt


def test_a_repair_in_a_follow_up_also_sees_the_history(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, IN_1997, IN_1997)
    ask(pipeline, "Top customers by revenue")
    executor.replies.append(db_error())
    ask(pipeline, "Only for 1997")
    repair_prompt = llm.calls[2].prompt
    assert "Question: Top customers by revenue" in repair_prompt
    assert ROUND_ERROR in repair_prompt  # and the error it must fix


# --- Nothing leaks from one turn into the next ------------------------------------------------


def test_a_failed_turn_never_answers_with_the_last_turns_rows(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP, *[IN_1997] * 3)
    ask(pipeline, "Top customers by revenue")  # turn 1 saves a result
    executor.replies.extend([db_error()] * 3)
    with pytest.raises(QueryExecutionError):  # not turn 1's result as the answer
        pipeline.ask("Only for 1997", conversation_id="c1", summarize_answer=False)


def test_each_turn_starts_with_the_full_attempt_budget(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP, TOP, TOP, IN_1997, IN_1997)
    executor.replies.extend([db_error(), db_error()])
    first = ask(pipeline, "Top customers by revenue")  # 2 failures, then the 3rd query runs
    assert first.sql_attempts == 3
    executor.replies.append(db_error())
    second = ask(pipeline, "Only for 1997")  # turn 1's failures don't count here
    assert (second.sql_attempts, len(second.failed_attempts)) == (2, 1)


def test_every_per_turn_field_is_reset() -> None:
    # A new AgentState field that isn't reset here would leak between turns: this test fails.
    start = new_turn(question="q", schema_text="s", table_names=[])
    assert set(start) == set(AgentState.__annotations__) - {"history"}


# --- What is kept, and for how long -----------------------------------------------------------


def test_history_keeps_the_last_turns_only() -> None:
    turns = [Turn(question=f"q{n}", kind="failed") for n in range(7)]
    kept: list[Turn] = []
    for turn in turns:
        kept = keep_last_turns(kept, [turn])
    assert [t.question for t in kept] == ["q2", "q3", "q4", "q5", "q6"]
    assert len(kept) == MAX_HISTORY_TURNS


def test_the_oldest_conversation_is_forgotten(
    executor: StubExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_module, "MAX_CONVERSATIONS", 2)
    pipeline, llm = pipeline_with(*[TOP] * 4)
    for conversation in ("a", "b", "c"):  # "c" pushes "a" out
        ask(pipeline, "Top customers by revenue", conversation_id=conversation)
    ask(pipeline, "Only for 1997", conversation_id="a")
    assert "<history>" not in llm.calls[3].prompt  # "a" starts again from nothing


def test_only_the_history_is_kept_between_turns(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP, TOP, TOP)
    ask(pipeline, "Top customers by revenue")
    ask(pipeline, "Only for 1997")
    ask(pipeline, "Standalone", conversation_id=None)  # its thread is deleted at once
    checkpoints = list(pipeline._checkpointer.list(None))
    assert len(checkpoints) == 1  # one conversation, one small checkpoint
    saved = checkpoints[0].checkpoint["channel_values"]
    assert [turn.question for turn in saved["history"]] == [
        "Top customers by revenue",
        "Only for 1997",
    ]
    assert "result" not in saved
    assert "Alfreds Futterkiste" not in repr(saved)  # no rows held in memory (§8.5)


def test_a_turn_that_fails_early_keeps_the_conversation(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, LLMProviderError("rate limited"), TOP)
    ask(pipeline, "Top customers by revenue")
    with pytest.raises(LLMProviderError):
        pipeline.ask("Only for 1997", conversation_id="c1")
    ask(pipeline, "Only for 1997")  # the retry still sees turn 1
    assert "Question: Top customers by revenue" in llm.calls[2].prompt


def test_saved_state_reloads_without_unregistered_types(executor: StubExecutor) -> None:
    # Every class in a checkpoint must be allowed (state.CHECKPOINT_TYPES), or LangGraph warns
    # now and will refuse to load it in a future version.
    pipeline, _ = pipeline_with(ASK_BACK, TOP, TOP, TOP)  # the failed query gets a repair
    executor.replies.append(db_error())  # so an attempt is saved too
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        pipeline.ask("Who are our best customers?", conversation_id="c1")
        ask(pipeline, "by total revenue")
        ask(pipeline, "Only for 1997")
    assert [str(w.message) for w in caught if "unregistered" in str(w.message)] == []


# --- How history reads in the prompt ----------------------------------------------------------


def test_each_kind_of_turn_is_written_plainly() -> None:
    history = [
        Turn(
            question="Top customers",
            kind="answer",
            sql="SELECT 1",
            columns=["company_name"],
            row_count=1,
        ),
        Turn(question="Best ones?", kind="clarification", reply="By what? Options: a; b"),
        Turn(question="Delete them", kind="refusal", reply="I can only read data."),
        Turn(question="Something odd", kind="failed"),
    ]
    assert format_history(history) == (
        "Question: Top customers\nSQL: SELECT 1\nResult: 1 row, columns: company_name\n\n"
        "Question: Best ones?\nYou asked back: By what? Options: a; b\n\n"
        "Question: Delete them\nYou refused: I can only read data.\n\n"
        "Question: Something odd\nNo answer: every query failed."
    )
