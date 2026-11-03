"""Rebuilding the agent's memory from saved turns (U3.2): after a restart, or once the in-memory
limit has pushed a conversation out. A fake LLM, a stubbed executor, no database.
"""

from contextlib import closing
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest

from querymind.agent import graph as graph_module
from querymind.agent import pipeline as pipeline_module
from querymind.agent.events import StepEvent
from querymind.agent.graph import turn_from
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AskResult, Clarification, SqlDraft, Turn
from querymind.agent.state import MAX_HISTORY_TURNS, AgentState
from querymind.store.conversations import (
    ConversationStore,
    SavedTurn,
    load_history,
    turn_from_saved,
)
from querymind.store.errors import StoreUnavailableError
from tests.unit.test_conversation import (
    ASK_BACK,
    IN_1997,
    SCHEMA,
    TOP,
    TOP_CUSTOMERS,
    StubExecutor,
    pipeline_with,
)

SAVED = Turn(
    question="Top customers by revenue",
    kind="answer",
    sql="SELECT company_name, revenue FROM customers LIMIT 1001",
    columns=["company_name", "revenue"],
    row_count=2,
)


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(graph_module, "execute_query", stub)
    monkeypatch.setattr(pipeline_module, "introspect_schema", lambda conn: SCHEMA)
    return stub


class Loader:
    """Stands in for the store: these turns for any conversation. Counts its calls."""

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.calls: list[str] = []

    def __call__(self, conversation_id: str) -> list[Turn]:
        self.calls.append(conversation_id)
        return self.turns


def ask(pipeline: Pipeline, question: str, conversation_id: str | None = "c1") -> AskResult:
    reply = pipeline.ask(question, conversation_id=conversation_id, summarize_answer=False)
    assert isinstance(reply, AskResult), reply
    return reply


def test_a_follow_up_after_a_restart_sees_the_saved_turn(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(IN_1997, history_loader=Loader(SAVED))

    ask(pipeline, "Only for 1997")

    prompt = llm.calls[0].prompt
    assert "Question: Top customers by revenue" in prompt
    assert f"SQL: {SAVED.sql}" in prompt
    assert "Result: 2 rows, columns: company_name, revenue" in prompt


def test_the_store_is_only_asked_when_the_memory_is_empty(executor: StubExecutor) -> None:
    loader = Loader(SAVED)
    pipeline, _ = pipeline_with(IN_1997, TOP, history_loader=loader)

    ask(pipeline, "Only for 1997")
    ask(pipeline, "Only for 1998")

    assert loader.calls == ["c1"]


def test_a_new_conversation_with_nothing_saved_gets_the_plain_prompt(
    executor: StubExecutor,
) -> None:
    pipeline, llm = pipeline_with(TOP, history_loader=Loader())

    ask(pipeline, "Top customers")

    assert "<history>" not in llm.calls[0].prompt


def test_a_standalone_question_never_asks_the_store(executor: StubExecutor) -> None:
    loader = Loader(SAVED)
    pipeline, llm = pipeline_with(TOP, history_loader=loader)

    ask(pipeline, "Top customers", conversation_id=None)

    assert loader.calls == []
    assert "<history>" not in llm.calls[0].prompt


def test_only_the_last_turns_reach_the_llm(executor: StubExecutor) -> None:
    saved = [
        Turn(question=f"Question {n}", kind="refusal", reply="No.")
        for n in range(MAX_HISTORY_TURNS + 3)
    ]
    pipeline, llm = pipeline_with(TOP, history_loader=Loader(*saved))

    ask(pipeline, "Next")

    assert "Question 0\n" not in llm.calls[0].prompt
    assert f"Question {MAX_HISTORY_TURNS + 2}" in llm.calls[0].prompt


def test_a_stopped_run_keeps_the_restored_memory(executor: StubExecutor) -> None:
    loader = Loader(SAVED)
    pipeline, llm = pipeline_with(IN_1997, TOP, history_loader=loader)
    with closing(pipeline.stream("STOPPED", conversation_id="c1")) as events:
        for event in events:
            if isinstance(event, StepEvent) and event.node == "run_sql":
                break

    ask(pipeline, "Next")

    assert "Question: Top customers by revenue" in llm.calls[1].prompt
    assert "STOPPED" not in llm.calls[1].prompt


def test_a_conversation_pushed_out_of_memory_comes_back_from_the_store(
    executor: StubExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Before U3.2 it was simply forgotten after MAX_CONVERSATIONS others.
    monkeypatch.setattr(pipeline_module, "MAX_CONVERSATIONS", 1)
    loader = Loader(SAVED)
    pipeline, llm = pipeline_with(TOP, TOP, IN_1997, history_loader=loader)

    ask(pipeline, "First", conversation_id="old")
    ask(pipeline, "Other", conversation_id="new")  # pushes "old" out
    ask(pipeline, "Back again", conversation_id="old")

    assert loader.calls == ["old", "new", "old"]
    assert "Question: Top customers by revenue" in llm.calls[2].prompt


# --- forget ----------------------------------------------------------------------------------


def test_forget_clears_the_memory(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, IN_1997)

    ask(pipeline, "Top customers")
    pipeline.forget("c1")
    ask(pipeline, "Only for 1997")

    assert "<history>" not in llm.calls[1].prompt


def test_forgetting_a_conversation_it_never_knew_is_fine(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with()

    pipeline.forget("never-seen")


def test_forget_also_makes_room_in_the_memory_list(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP)

    ask(pipeline, "Top customers")
    pipeline.forget("c1")

    assert "c1" not in pipeline._conversations


# --- A saved turn reads exactly like a live one --------------------------------------------------


def saved(kind: Any, sql: str | None, reply: dict[str, Any], question: str = "q") -> SavedTurn:
    return SavedTurn(
        n=1,
        question=question,
        kind=kind,
        sql=sql,
        reply=reply,
        trace=[],
        created_at=datetime.now(UTC),
    )


def test_a_saved_answer_is_the_same_memory_as_a_live_one() -> None:
    state: AgentState = {"question": "q", "sql": "SELECT 1", "result": TOP_CUSTOMERS}

    from_store = turn_from_saved(
        saved("answer", "SELECT 1", {"columns": TOP_CUSTOMERS.columns, "row_count": 2})
    )

    assert from_store == turn_from(state)


def test_a_saved_question_back_is_the_same_memory_as_a_live_one() -> None:
    assert ASK_BACK.clarification is not None
    state: AgentState = {"question": "q", "draft": ASK_BACK}

    from_store = turn_from_saved(
        saved(
            "clarification",
            None,
            {
                "clarifying_question": ASK_BACK.clarification.question,
                "options": ASK_BACK.clarification.options,
            },
        )
    )

    assert from_store == turn_from(state)


def test_a_saved_refusal_is_the_same_memory_as_a_live_one() -> None:
    refusal = SqlDraft(refusal="I only read.", sql="", explanation="", tables_used=[])
    state: AgentState = {"question": "q", "draft": refusal}

    assert turn_from_saved(saved("refusal", None, {"message": "I only read."})) == turn_from(state)


def test_clarification_memory_text() -> None:
    asked = Clarification(question="By what?", options=["orders", "revenue"])

    assert asked.memory_text() == "By what? Options: orders; revenue"


# --- load_history ------------------------------------------------------------------------------


class FakeStore:
    def __init__(self, *turns: SavedTurn, error: Exception | None = None) -> None:
        self.turns = list(turns)
        self.error = error
        self.limits: list[int] = []

    def recent_turns(self, conversation_id: Any, *, limit: int) -> list[SavedTurn]:
        self.limits.append(limit)
        if self.error is not None:
            raise self.error
        return self.turns


def test_load_history_turns_saved_turns_into_memory() -> None:
    store = FakeStore(saved("refusal", None, {"message": "No."}, question="Delete it"))

    turns = load_history(cast(ConversationStore, store), str(uuid4()))

    assert turns == [Turn(question="Delete it", kind="refusal", reply="No.")]
    assert store.limits == [MAX_HISTORY_TURNS]


def test_a_failing_store_means_no_memory_not_a_failed_question() -> None:
    store = FakeStore(error=StoreUnavailableError("down"))

    assert load_history(cast(ConversationStore, store), str(uuid4())) == []
