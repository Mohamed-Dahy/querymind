"""Pipeline.stream (U2.1): the events of a run, in order, with a fake LLM and a stubbed executor.

What matters most here: `ask()` keeps behaving as before (the older test files check that, and
stay untouched), nothing private reaches an event (§8.5), and a stopped run still writes its
audit line (§8.7) without damaging the conversation.
"""

import logging
import threading
from contextlib import closing
from typing import Any

import pytest

from querymind.agent import graph as graph_module
from querymind.agent import pipeline as pipeline_module
from querymind.agent.events import (
    VALIDATED_SIGNAL,
    AgentEvent,
    AttemptFailedEvent,
    CheckedEvent,
    ExamplesEvent,
    Finished,
    RewriteEvent,
    SchemaEvent,
    SqlEvent,
    StepEvent,
    TraceBuilder,
    ValidatedEvent,
)
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import (
    AskResult,
    ClarificationRequest,
    FewShotExample,
    Refusal,
    SanityWarning,
    SqlAttempt,
    SqlDraft,
)
from querymind.db.errors import QueryExecutionError
from querymind.db.executor import QueryResult
from querymind.db.schema import describe_schema
from querymind.llm import LLMProviderError
from querymind.semantic.retrieval import SchemaSelection
from tests.unit.test_conversation import (
    ASK_BACK,
    IN_1997,
    SCHEMA,
    TOP,
    TOP_CUSTOMERS,
    StubExecutor,
    draft,
    pipeline_with,
)

REFUSE = SqlDraft(refusal="I can only read data.", sql="", explanation="", tables_used=[])
EMPTY = QueryResult(columns=["company_name"], rows=[], row_count=0, truncated=False, duration_ms=1)
# The Postgres text meant for the LLM; a person gets the message.
PRIVATE_DETAIL = "PRIVATE-DETAIL relation at line 7"


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(graph_module, "execute_query", stub)
    monkeypatch.setattr(pipeline_module, "introspect_schema", lambda conn: SCHEMA)
    return stub


def failed_query() -> QueryExecutionError:
    return QueryExecutionError("The database could not run the query.", detail=PRIVATE_DETAIL)


def short(event: AgentEvent | Finished) -> str:
    """One event as a few words, so a whole run fits on a screen in the assertions."""
    match event:
        case StepEvent(node=node, status=status):
            return f"{node} {status}"
        case SqlEvent(attempt=attempt):
            return f"sql {attempt}"
        case ValidatedEvent(attempt=attempt):
            return f"validated {attempt}"
        case AttemptFailedEvent(attempt=attempt, kind=kind):
            return f"failed {attempt} {kind}"
        case Finished():
            return "reply"
        case _:
            return event.type


def run(pipeline: Pipeline, question: str = "Top customers") -> list[AgentEvent | Finished]:
    return list(pipeline.stream(question, conversation_id="c1", summarize_answer=False))


FIRST_TRY = [
    "retrieve_schema started",
    "schema",
    "retrieve_schema finished",
    "generate_sql started",
    "sql 1",
    "generate_sql finished",
    "run_sql started",
    "validated 1",
    "run_sql finished",
]


# --- The order of events ---------------------------------------------------------------------


def test_a_clean_answer(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP)
    events = run(pipeline)

    assert [short(e) for e in events] == [
        *FIRST_TRY,
        "check_result started",
        "checked",
        "check_result finished",
        "summarize started",
        "summarize finished",
        "reply",
    ]
    finished = events[-1]
    assert isinstance(finished, Finished) and isinstance(finished.reply, AskResult)
    assert finished.reply.result == TOP_CUSTOMERS


def test_a_failed_query_is_repaired_and_runs_again(executor: StubExecutor) -> None:
    executor.replies.append(failed_query())
    pipeline, _ = pipeline_with(TOP, IN_1997)

    assert [short(e) for e in run(pipeline)] == [
        "retrieve_schema started",
        "schema",
        "retrieve_schema finished",
        "generate_sql started",
        "sql 1",
        "generate_sql finished",
        "run_sql started",
        "validated 1",
        "failed 1 db_error",
        "run_sql finished",
        "repair_sql started",
        "sql 2",
        "repair_sql finished",
        "run_sql started",
        "validated 2",
        "run_sql finished",
        "check_result started",
        "checked",
        "check_result finished",
        "summarize started",
        "summarize finished",
        "reply",
    ]


def test_unsafe_sql_is_never_reported_as_validated(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(draft("DELETE FROM customers"), TOP)
    names = [short(e) for e in run(pipeline)]

    assert names[names.index("run_sql started") + 1] == "failed 1 unsafe"
    assert "validated 1" not in names
    assert "validated 2" in names
    assert executor.queries[0].sql.startswith("SELECT")  # the DELETE never reached the database


def test_a_result_that_looks_wrong_gets_a_warning_and_a_repair(executor: StubExecutor) -> None:
    executor.replies.append(EMPTY)
    pipeline, _ = pipeline_with(TOP, IN_1997)
    events = run(pipeline)
    names = [short(e) for e in events]

    assert names[names.index("checked") + 1] == "failed 1 suspicious"
    assert names[names.index("failed 1 suspicious") + 2] == "repair_sql started"
    checked = next(e for e in events if isinstance(e, CheckedEvent))
    assert [w.code for w in checked.warnings] == ["empty"]


@pytest.mark.parametrize(
    ("response", "kind"), [(ASK_BACK, ClarificationRequest), (REFUSE, Refusal)]
)
def test_asking_back_or_refusing_has_no_sql_events(
    executor: StubExecutor, response: SqlDraft, kind: type
) -> None:
    pipeline, _ = pipeline_with(response)
    events = run(pipeline)

    assert [short(e) for e in events] == [
        "retrieve_schema started",
        "schema",
        "retrieve_schema finished",
        "generate_sql started",
        "generate_sql finished",
        "reply",
    ]
    finished = events[-1]
    assert isinstance(finished, Finished) and isinstance(finished.reply, kind)


def test_an_llm_error_marks_the_step_failed_and_is_raised(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(LLMProviderError("429 from the vendor"))
    seen: list[AgentEvent | Finished] = []

    with pytest.raises(LLMProviderError):
        for event in pipeline.stream("Top customers", conversation_id="c1"):
            seen.append(event)

    assert [short(e) for e in seen][-2:] == ["generate_sql started", "generate_sql failed"]
    assert not any(isinstance(e, Finished) for e in seen)


def test_every_finished_step_says_how_long_it_took(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP)
    steps = [e for e in run(pipeline) if isinstance(e, StepEvent)]

    for step in steps:
        assert (step.duration_ms is None) == (step.status == "started")


# --- What an event may hold ------------------------------------------------------------------


def test_no_prompt_schema_text_detail_or_rows_in_any_event(executor: StubExecutor) -> None:
    executor.replies.extend([failed_query(), EMPTY])
    pipeline, llm = pipeline_with(TOP, IN_1997, TOP)
    events = run(pipeline)
    text = " ".join(e.model_dump_json() for e in events if not isinstance(e, Finished))

    assert PRIVATE_DETAIL not in text
    assert describe_schema(SCHEMA) not in text
    assert not any(call.prompt in text for call in llm.calls)
    for value in ("Alfreds Futterkiste", "Ernst Handel", "12345.67"):
        assert value not in text


def test_a_warning_event_leaves_out_the_hint_for_the_llm() -> None:
    warning = SanityWarning(code="empty", message="No rows came back.", detail="PRIVATE hint")
    writes = {"warnings": [warning]}
    chunks = TraceBuilder(total_tables=2)
    chunks.events("tasks", {"id": "t", "name": "check_result", "input": {}})

    events = chunks.events(
        "tasks", {"id": "t", "name": "check_result", "error": None, "result": writes}
    )

    assert "PRIVATE" not in " ".join(e.model_dump_json() for e in events)
    assert isinstance(events[0], CheckedEvent)
    assert events[0].warnings[0].message == "No rows came back."


def test_the_retrieval_details_leave_out_the_raw_fallback_reason() -> None:
    selection = SchemaSelection(
        mode="fallback",
        tables=("customers", "orders"),
        reason="ClientError: 500 PRIVATE vendor text",
        schema_tokens=100,
        search="hybrid",
    )
    example = FewShotExample(id="e1", question="Sales per year?", sql="SELECT 1")
    writes: dict[str, Any] = {
        "schema_selection": selection,
        "search_question": "Top customers in 1997?",
        "examples": [example],
    }
    chunks = TraceBuilder(total_tables=13)
    chunks.events("tasks", {"id": "t", "name": "retrieve_schema", "input": {}})

    events = chunks.events(
        "tasks", {"id": "t", "name": "retrieve_schema", "error": None, "result": writes}
    )

    assert "PRIVATE" not in " ".join(e.model_dump_json() for e in events)
    schema = events[0]
    assert isinstance(schema, SchemaEvent)
    assert schema.schema_selection.total_tables == 13
    assert schema.schema_selection.mode == "fallback"
    assert isinstance(events[1], RewriteEvent)
    assert events[1].search_question == "Top customers in 1997?"
    assert isinstance(events[2], ExamplesEvent)
    assert events[2].questions == ["Sales per year?"]  # the question, never the example's SQL


def test_an_attempt_event_leaves_out_the_detail() -> None:
    attempt = SqlAttempt(sql="SELECT 1", error_kind="db_error", message="It failed.", detail="X9")
    chunks = TraceBuilder(total_tables=2)
    chunks.events("tasks", {"id": "t", "name": "run_sql", "input": {}})

    events = chunks.events(
        "tasks", {"id": "t", "name": "run_sql", "error": None, "result": {"attempts": [attempt]}}
    )

    assert isinstance(events[0], AttemptFailedEvent)
    assert "X9" not in events[0].model_dump_json()


def test_other_custom_chunks_and_hidden_nodes_make_no_events() -> None:
    chunks = TraceBuilder(total_tables=2)

    assert chunks.events("custom", "something else") == []
    assert chunks.events("tasks", {"id": "t", "name": "remember", "input": {}}) == []
    assert isinstance(chunks.events("custom", VALIDATED_SIGNAL)[0], ValidatedEvent)


# --- ask() and the reply ---------------------------------------------------------------------


def test_ask_returns_the_stream_s_reply_with_what_the_drawer_needs(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP)

    reply = pipeline.ask("Top customers", conversation_id="c1", summarize_answer=False)

    assert isinstance(reply, AskResult)
    assert reply.schema_selection is not None
    assert reply.schema_selection.mode == "full"
    assert reply.schema_selection.tables == ["customers", "orders"]
    assert reply.schema_selection.total_tables == 2
    assert reply.search_question is None
    assert reply.example_questions == ()
    assert reply.duration_ms is not None


# --- Stopping --------------------------------------------------------------------------------


def stop_after(pipeline: Pipeline, name: str, *, question: str, conversation_id: str) -> None:
    """Run until the event `name`, then close the stream, as the API does on a disconnect."""
    with closing(pipeline.stream(question, conversation_id=conversation_id)) as events:
        for event in events:
            if short(event) == name:
                break
        else:
            pytest.fail(f"no {name} event")


def audit_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "querymind.audit"]


def test_a_stopped_run_still_writes_its_audit_line(
    executor: StubExecutor, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline, _ = pipeline_with(TOP, IN_1997)

    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        stop_after(pipeline, "sql 1", question="Top customers", conversation_id="c1")

    [line] = audit_lines(caplog)
    assert getattr(line, "outcome", None) == "cancelled"
    assert getattr(line, "question", None) == "Top customers"
    assert getattr(line, "conversation_id", None) == "c1"


def test_a_stopped_run_leaves_the_conversation_as_it_was(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with(TOP, IN_1997, TOP)

    pipeline.ask("Top customers", conversation_id="c1", summarize_answer=False)
    stop_after(pipeline, "run_sql started", question="STOPPED question", conversation_id="c1")
    reply = pipeline.ask("Only for 1997", conversation_id="c1", summarize_answer=False)

    assert isinstance(reply, AskResult)
    follow_up = llm.calls[2].prompt
    assert "Question: Top customers" in follow_up  # the earlier turn is still remembered
    assert "STOPPED question" not in follow_up  # the stopped turn left nothing behind


def test_a_second_run_of_a_conversation_waits_for_the_first(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP, IN_1997)
    first = pipeline.stream("Top customers", conversation_id="c1", summarize_answer=False)
    assert short(next(first)) == "retrieve_schema started"  # the first run is now under way
    done = threading.Event()

    def second() -> None:
        pipeline.ask("Only for 1997", conversation_id="c1", summarize_answer=False)
        done.set()

    thread = threading.Thread(target=second)
    thread.start()
    assert not done.wait(0.2)  # still waiting

    first.close()
    assert done.wait(5)  # the stop lets it go
    thread.join()


def test_a_standalone_question_never_waits(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with(TOP, IN_1997)
    first = pipeline.stream("Top customers", conversation_id="c1", summarize_answer=False)
    next(first)
    done = threading.Event()

    def standalone() -> None:
        pipeline.ask("How many orders?", summarize_answer=False)
        done.set()

    thread = threading.Thread(target=standalone)
    thread.start()
    assert done.wait(5)
    thread.join()
    first.close()
