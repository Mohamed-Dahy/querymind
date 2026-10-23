"""The agent graph with a fake LLM and a stubbed executor: no DB, no network.

Wiring (2.1), the self-correction loop (2.2) and the sanity check (2.3). The real DB + graph
path is covered by tests/integration/test_pipeline.py.
"""

from collections import deque
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import BaseModel, SecretStr

from querymind.agent import graph as graph_module
from querymind.agent.graph import build_graph
from querymind.agent.schemas import AnswerSummary, Clarification, SqlAttempt, SqlDraft
from querymind.agent.state import AgentContext, AgentState
from querymind.config import Settings
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.db.executor import QueryResult
from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.sql_validator import ValidatedQuery

RESULT = QueryResult(columns=["n"], rows=[[91]], row_count=1, truncated=False, duration_ms=2)
EMPTY = QueryResult(columns=["n"], rows=[], row_count=0, truncated=False, duration_ms=2)
DOUBLED = QueryResult(
    columns=["city"], rows=[["Berlin"], ["Berlin"]], row_count=2, truncated=False, duration_ms=2
)
NEGATIVE = QueryResult(
    columns=["total_sales"], rows=[[-5.0]], row_count=1, truncated=False, duration_ms=2
)
START: AgentState = {
    "question": "How many customers are there?",
    "schema_text": "Table customers (customer_id text PK, ...)",
    "table_names": ["customers", "orders"],
}
ROUND_ERROR = "function round(double precision, integer) does not exist"


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
        "max_rows": 1000,
        "max_sql_attempts": 3,
    }
    return Settings.model_validate(values | overrides)


class StubExecutor:
    """Stands in for execute_query: records each query; answers with the next queued reply
    (a result to return, or an error to raise), and RESULT once the queue is empty."""

    def __init__(self) -> None:
        self.queries: list[ValidatedQuery] = []
        self.replies: deque[QueryResult | Exception] = deque()

    def __call__(
        self, conn: psycopg.Connection, query: ValidatedQuery, *, timeout_seconds: int
    ) -> QueryResult:
        self.queries.append(query)
        reply = self.replies.popleft() if self.replies else RESULT
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(graph_module, "execute_query", stub)
    return stub


def context(llm: FakeLLMProvider, connect: MagicMock | None = None, **options: Any) -> AgentContext:
    attempts = options.pop("max_sql_attempts", 3)
    return AgentContext(
        llm=llm,
        settings=settings(max_sql_attempts=attempts),
        connect=connect or MagicMock(),
        **options,
    )


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Counts the customers.", tables_used=["customers"])


GOOD = draft("SELECT count(*) AS n FROM customers")


def answering_llm() -> FakeLLMProvider:
    """A fake LLM that writes a count query, then summarizes it."""
    return FakeLLMProvider([GOOD, "There are 91."])


def db_error(detail: str = ROUND_ERROR) -> QueryExecutionError:
    return QueryExecutionError(f"The database could not run the query: {detail}", detail=detail)


# --- Wiring (2.1) ----------------------------------------------------------------------------


def test_nodes_run_in_order_and_fill_the_state(executor: StubExecutor) -> None:
    steps = [
        next(iter(update))  # "updates" mode yields {node name: fields it returned}
        for update in build_graph().stream(
            START, context=context(answering_llm()), stream_mode="updates"
        )
    ]
    assert steps == [
        "retrieve_schema",  # 3.3; with no retriever it only records "whole schema"
        "generate_sql",
        "run_sql",
        "check_result",
        "summarize",
        "remember",
    ]

    final = build_graph().invoke(START, context=context(answering_llm()))
    assert final["sql"] == "SELECT COUNT(*) AS n FROM customers LIMIT 1001"  # the validated SQL
    assert final["explanation"] == "Counts the customers."
    assert final["result"] == RESULT
    assert final["answer"] == AnswerSummary(text="There are 91.", from_llm=True)
    assert final.get("attempts", []) == []  # right first time
    assert final["warnings"] == []  # and nothing looks wrong


def test_only_validated_sql_reaches_the_executor(executor: StubExecutor) -> None:
    build_graph().invoke(START, context=context(answering_llm()))
    (query,) = executor.queries
    assert isinstance(query, ValidatedQuery)
    assert query.sql.endswith("LIMIT 1001")


def test_unsafe_sql_stops_before_any_connection(executor: StubExecutor) -> None:
    llm = FakeLLMProvider([draft("DELETE FROM customers")])
    connect = MagicMock()
    final = build_graph().invoke(START, context=context(llm, connect, max_sql_attempts=1))
    connect.assert_not_called()  # validation comes first: no connection is even opened
    assert executor.queries == []
    assert "result" not in final  # gave up (1 attempt allowed): Pipeline.ask raises
    assert final["attempts"][0].error_kind == "unsafe"
    assert len(llm.calls) == 1  # and no summary call


def test_the_connection_is_closed_after_running(executor: StubExecutor) -> None:
    connect = MagicMock()
    build_graph().invoke(START, context=context(answering_llm(), connect))
    connect.assert_called_once()
    connect.return_value.__exit__.assert_called_once()  # the `with` block closed it


def test_summary_can_be_skipped(executor: StubExecutor) -> None:
    llm = FakeLLMProvider([GOOD])
    final = build_graph().invoke(START, context=context(llm, summarize_answer=False))
    assert final["answer"] == AnswerSummary(text="n: 91", from_llm=False)
    assert len(llm.calls) == 1  # only the SQL call


def test_state_holds_only_saveable_values(executor: StubExecutor) -> None:
    # Phase 2.6 saves the state between turns. A connection can't be saved, and a reloaded
    # ValidatedQuery would carry the validator's stamp without having been validated.
    executor.replies.append(db_error())  # so the state also holds an attempt
    llm = FakeLLMProvider([GOOD, GOOD, "There are 91."])
    final = build_graph().invoke(START, context=context(llm))
    for key, value in final.items():
        assert value is None or isinstance(value, str | bool | list | BaseModel), key
        assert not isinstance(value, ValidatedQuery), key
    assert all(isinstance(a, SqlAttempt) for a in final["attempts"])


def test_diagram_shows_the_nodes_and_edges() -> None:
    mermaid = build_graph().get_graph().draw_mermaid()
    for edge in [
        "__start__ --> retrieve_schema",  # 3.3: pick the tables first
        "retrieve_schema --> generate_sql",
        "generate_sql -.-> run_sql",
        "generate_sql -.-> remember",  # it refused or asked back: no SQL runs
        "run_sql -.-> check_result",  # dotted = a conditional edge (one of these is taken)
        "run_sql -.-> repair_sql",
        "run_sql -.-> summarize",
        "run_sql -.-> remember",  # every try failed
        "check_result -.-> summarize",
        "check_result -.-> repair_sql",
        "repair_sql -.-> run_sql",  # the self-correction loop
        "summarize --> remember",
        "remember --> __end__",  # every turn is written to the history (2.6)
    ]:
        assert edge in mermaid


# --- Self-correction (2.2) -------------------------------------------------------------------


def test_database_error_is_sent_back_and_fixed(executor: StubExecutor) -> None:
    executor.replies.append(db_error())
    llm = FakeLLMProvider([draft("SELECT freight AS n FROM orders"), GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))

    assert final["result"] == RESULT
    (attempt,) = final["attempts"]
    assert attempt.error_kind == "db_error"
    assert attempt.detail == ROUND_ERROR
    # The SQL recorded is the one that really ran: validated, with its LIMIT.
    assert attempt.sql == "SELECT freight AS n FROM orders LIMIT 1001"
    # Calls: generate, repair, summary. The repair prompt shows the failed SQL and the error.
    generate_call, repair_call, _summary = llm.calls
    assert "earlier queries" not in generate_call.prompt
    assert attempt.sql in repair_call.prompt
    assert f"PostgreSQL returned an error: {ROUND_ERROR}" in repair_call.prompt
    assert "Table customers" in repair_call.prompt  # the schema is still there


def test_unsafe_sql_is_sent_back_with_the_reason(executor: StubExecutor) -> None:
    llm = FakeLLMProvider([draft("DELETE FROM customers"), GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))
    assert final["result"] == RESULT
    assert executor.queries[0].sql.startswith("SELECT COUNT(*)")  # only the fixed one ran
    repair_prompt = llm.calls[1].prompt
    assert "The safety check rejected it:" in repair_prompt
    assert "DELETE FROM customers" in repair_prompt


def test_timeout_asks_for_a_simpler_query(executor: StubExecutor) -> None:
    executor.replies.append(QueryTimeoutError("The query took longer than 10 s and was stopped."))
    llm = FakeLLMProvider([GOOD, GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))
    assert final["attempts"][0].error_kind == "timeout"
    assert "Write a simpler, faster query" in llm.calls[1].prompt


def test_a_repaired_query_is_validated_again(executor: StubExecutor) -> None:
    # The repair is a new draft: it gets no free pass. Here it's unsafe, so it never runs.
    executor.replies.append(db_error())
    llm = FakeLLMProvider([GOOD, draft("DROP TABLE customers"), GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))
    assert [a.error_kind for a in final["attempts"]] == ["db_error", "unsafe"]
    assert len(executor.queries) == 2  # the first and the third; the DROP never reached it
    # The third prompt lists both failures, in order.
    third = llm.calls[2].prompt
    assert third.index("Attempt 1:") < third.index("Attempt 2:")


def test_gives_up_after_max_attempts(executor: StubExecutor) -> None:
    executor.replies.extend([db_error(), db_error(), db_error()])
    llm = FakeLLMProvider([GOOD, GOOD, GOOD])
    final = build_graph().invoke(START, context=context(llm))
    assert "result" not in final
    assert len(final["attempts"]) == 3
    assert len(llm.calls) == 3  # generate + 2 repairs, and no summary


def test_one_attempt_means_no_repair(executor: StubExecutor) -> None:
    executor.replies.append(db_error())
    llm = FakeLLMProvider([GOOD])
    final = build_graph().invoke(START, context=context(llm, max_sql_attempts=1))
    assert len(final["attempts"]) == 1
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "error", [LLMProviderError("429 rate limited"), LLMOutputError("not the schema")]
)
def test_llm_errors_are_not_retried_by_the_loop(executor: StubExecutor, error: Exception) -> None:
    # A failing LLM isn't fixed by asking it again right away: the run ends (the eval backs off).
    executor.replies.append(db_error())
    llm = FakeLLMProvider([GOOD, error])
    with pytest.raises(type(error)):
        build_graph().invoke(START, context=context(llm))
    assert len(llm.calls) == 2


def test_database_down_is_not_retried_by_the_loop(executor: StubExecutor) -> None:
    connect = MagicMock(side_effect=DatabaseUnavailableError("Could not connect to the database."))
    llm = FakeLLMProvider([GOOD])
    with pytest.raises(DatabaseUnavailableError):
        build_graph().invoke(START, context=context(llm, connect))
    assert len(llm.calls) == 1  # no repair: different SQL won't bring the database back


# --- Sanity check (2.3) ----------------------------------------------------------------------


def test_an_empty_result_gets_one_repair(executor: StubExecutor) -> None:
    executor.replies.append(EMPTY)  # then RESULT
    llm = FakeLLMProvider([GOOD, GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))
    assert final["result"] == RESULT
    assert final["warnings"] == []  # the repaired result looks fine
    (suspicious,) = final["attempts"]
    assert suspicious.error_kind == "suspicious"
    assert "It ran, but the result looks wrong: The query ran but returned no rows" in (
        llm.calls[1].prompt
    )


def test_still_empty_after_the_repair_is_answered_with_a_warning(executor: StubExecutor) -> None:
    # "Which products were never ordered?" can truly be empty: one repair, then we answer.
    executor.replies.extend([EMPTY, EMPTY])
    llm = FakeLLMProvider([GOOD, GOOD])
    final = build_graph().invoke(START, context=context(llm))
    assert final["result"] == EMPTY
    assert [w.code for w in final["warnings"]] == ["empty"]
    # Generate + ONE repair. No second sanity repair, and no summary call: an empty result
    # gets the templated answer (Phase 1 rule).
    assert len(llm.calls) == 2


def test_duplicate_rows_get_one_repair(executor: StubExecutor) -> None:
    executor.replies.append(DOUBLED)
    llm = FakeLLMProvider([GOOD, GOOD, "91."])
    final = build_graph().invoke(START, context=context(llm))
    assert final["result"] == RESULT
    assert "a join multiplied rows" in llm.calls[1].prompt


def test_a_negative_total_is_only_a_warning(executor: StubExecutor) -> None:
    executor.replies.append(NEGATIVE)
    llm = FakeLLMProvider([GOOD, "Total sales: -5."])
    final = build_graph().invoke(START, context=context(llm))
    assert [w.code for w in final["warnings"]] == ["negative_total"]
    assert final.get("attempts", []) == []  # no repair
    assert len(llm.calls) == 2


def test_a_failed_repair_keeps_the_first_result(executor: StubExecutor) -> None:
    # Empty result → repair → the repairs fail in the database → answer the empty result.
    executor.replies.extend([EMPTY, db_error(), db_error()])
    llm = FakeLLMProvider([GOOD, GOOD, GOOD])
    final = build_graph().invoke(START, context=context(llm))
    assert final["result"] == EMPTY
    assert final["sql"] == "SELECT COUNT(*) AS n FROM customers LIMIT 1001"  # its own SQL
    assert [w.code for w in final["warnings"]] == ["empty"]
    assert [a.error_kind for a in final["attempts"]] == ["suspicious", "db_error", "db_error"]
    assert final["answer"].from_llm is False  # empty → templated answer


def test_no_sanity_repair_without_tries_left(executor: StubExecutor) -> None:
    executor.replies.append(EMPTY)
    llm = FakeLLMProvider([GOOD])
    final = build_graph().invoke(START, context=context(llm, max_sql_attempts=1))
    assert [w.code for w in final["warnings"]] == ["empty"]
    assert len(llm.calls) == 1  # no repair: the one allowed query was used


# --- Clarify or refuse (2.4) -----------------------------------------------------------------

ASKS = SqlDraft(
    sql="",
    explanation="",
    tables_used=[],
    clarification=Clarification(question="Best by what?", options=["revenue", "orders"]),
)
REFUSES = SqlDraft(sql="", explanation="", tables_used=[], refusal="I can only read data.")


@pytest.mark.parametrize("reply", [ASKS, REFUSES], ids=["clarification", "refusal"])
def test_asking_back_or_refusing_runs_no_sql(executor: StubExecutor, reply: SqlDraft) -> None:
    connect = MagicMock()
    final = build_graph().invoke(START, context=context(FakeLLMProvider([reply]), connect))
    assert final["draft"] == reply
    assert "result" not in final
    connect.assert_not_called()
    assert executor.queries == []


def test_a_refusal_wins_over_sql_in_the_same_draft(executor: StubExecutor) -> None:
    both = SqlDraft(sql="SELECT 1", explanation="", tables_used=[], refusal="No.")
    build_graph().invoke(START, context=context(FakeLLMProvider([both])))
    assert executor.queries == []


def test_a_repair_that_asks_back_ends_the_run(executor: StubExecutor) -> None:
    executor.replies.append(db_error())
    final = build_graph().invoke(START, context=context(FakeLLMProvider([GOOD, ASKS])))
    assert final["draft"] == ASKS
    assert "result" not in final  # Pipeline.ask returns the clarifying question


def test_a_repair_that_asks_back_keeps_an_earlier_result(executor: StubExecutor) -> None:
    executor.replies.append(EMPTY)  # suspicious → repair → the LLM asks back instead
    final = build_graph().invoke(START, context=context(FakeLLMProvider([GOOD, ASKS])))
    assert final["result"] == EMPTY  # answered with the result we have, and its warning
    assert [w.code for w in final["warnings"]] == ["empty"]
