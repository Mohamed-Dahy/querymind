"""The whole Phase 1 pipeline on the real database, with a scripted fake LLM (no network).

Real: schema introspection, validator, executor, Postgres. Fake: the two LLM calls (SQL, answer).
"""

import logging

import psycopg
import pytest

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import (
    AskResult,
    Clarification,
    ClarificationRequest,
    Refusal,
    SqlDraft,
)
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import QueryExecutionError
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError

pytestmark = pytest.mark.integration


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Test query.", tables_used=[])


def ask(pipeline: Pipeline, question: str, *, summarize_answer: bool = True) -> AskResult:
    """Ask, and check the reply is an answer (not a clarifying question or a refusal)."""
    reply = pipeline.ask(question, summarize_answer=summarize_answer)
    assert isinstance(reply, AskResult), reply
    return reply


def make_pipeline(settings: Settings, *responses: object) -> tuple[Pipeline, FakeLLMProvider]:
    fake = FakeLLMProvider(responses)  # type: ignore[arg-type]
    return Pipeline(llm=fake, settings=settings), fake


def test_question_to_answer(settings: Settings) -> None:
    pipeline, fake = make_pipeline(
        settings,
        draft("SELECT count(*) AS customer_count FROM customers"),
        "There are 91 customers.",
    )
    result = ask(pipeline, "How many customers are there?")

    assert result.result.rows == [[91]]
    assert result.answer.text == "There are 91 customers."
    assert result.answer.from_llm is True
    # The SQL reported is the validated one that really ran, LIMIT included.
    assert (
        result.sql
        == f"SELECT COUNT(*) AS customer_count FROM customers LIMIT {settings.max_rows + 1}"
    )
    assert result.explanation == "Test query."
    # First call: SQL with the real Northwind schema in the prompt. Second: the summary.
    sql_call, summary_call = fake.calls
    assert "Table customers" in sql_call.prompt
    assert summary_call.schema is None and "91" in summary_call.prompt


def test_invented_number_in_the_answer_is_replaced(settings: Settings) -> None:
    pipeline, _ = make_pipeline(
        settings,
        draft("SELECT count(*) AS customer_count FROM customers"),
        "There are 97 customers.",
    )
    result = ask(pipeline, "How many customers are there?")
    assert result.answer.from_llm is False
    assert result.answer.text == "customer_count: 91"


def test_unsafe_sql_is_rejected_before_it_runs(settings: Settings) -> None:
    # The LLM insists on DELETE: each repair is rejected too, then the last error is raised.
    pipeline, fake = make_pipeline(settings, *[draft("DELETE FROM orders")] * 3)
    with pytest.raises(UnsafeSqlError, match="Only SELECT"):
        pipeline.ask("Delete all orders")
    assert len(fake.calls) == settings.max_sql_attempts  # generate + 2 repairs, no summary
    with connect_readonly(settings) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (830,)


def test_database_errors_propagate_cleanly(settings: Settings) -> None:
    pipeline, _ = make_pipeline(settings, *[draft("SELECT no_such_column FROM orders")] * 3)
    with pytest.raises(QueryExecutionError, match="does not exist"):
        pipeline.ask("Something odd")


def test_a_real_postgres_error_is_fixed_by_a_repair(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    # Phase 1's one golden miss (h07) was this kind of error: Postgres's round(x, 2) needs a
    # numeric, and freight is `real`. (sqlglot adds the cast itself only when it can see the
    # type, e.g. after ::float8; it can't see column types.)
    bad = "SELECT round(sum(freight), 2) AS total_freight FROM orders"
    good = "SELECT round(sum(freight)::numeric, 2) AS total_freight FROM orders"
    pipeline, fake = make_pipeline(settings, draft(bad), draft(good), "Total freight: 64942.70.")
    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        result = ask(pipeline, "What is the total freight?")

    assert result.result.rows == [[64942.7]]
    assert result.sql_attempts == 2
    (failed,) = result.failed_attempts
    assert failed.detail == "function round(real, integer) does not exist"
    assert failed.detail in fake.calls[1].prompt  # Postgres's own words reached the LLM
    (audit,) = [r for r in caplog.records if r.name == "querymind.audit"]
    assert audit.sql_attempts == 2  # type: ignore[attr-defined]
    assert audit.attempt_errors == [f"db_error: {failed.detail}"]  # type: ignore[attr-defined]


def test_an_empty_result_is_retried_with_a_hint(settings: Settings) -> None:
    # A classic: the user typed "germany", the data says "Germany", '=' is case-sensitive.
    wrong = "SELECT company_name FROM customers WHERE country = 'germany'"
    right = "SELECT company_name FROM customers WHERE country ILIKE 'germany'"
    pipeline, fake = make_pipeline(settings, draft(wrong), draft(right), "There are 11.")
    result = ask(pipeline, "Which customers are in germany?")

    assert result.result.row_count == 11
    assert result.warnings == ()  # the repaired result looks fine
    assert result.sql_attempts == 2
    (suspicious,) = result.failed_attempts
    assert suspicious.error_kind == "suspicious"
    assert "returned no rows" in fake.calls[1].prompt


def test_audit_keeps_the_validated_sql_when_the_database_fails(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline, _ = make_pipeline(settings, *[draft("SELECT no_such_column FROM orders")] * 3)
    with (
        caplog.at_level(logging.INFO, logger="querymind.audit"),
        pytest.raises(QueryExecutionError),
    ):
        pipeline.ask("Something odd")
    (audit,) = [r for r in caplog.records if r.name == "querymind.audit"]
    assert audit.sql == f"SELECT no_such_column FROM orders LIMIT {settings.max_rows + 1}"  # type: ignore[attr-defined]
    assert audit.sql_attempts == 3  # type: ignore[attr-defined]


def test_schema_is_read_once(settings: Settings) -> None:
    connections = 0

    def counting_connect(s: Settings) -> psycopg.Connection:
        nonlocal connections
        connections += 1
        return connect_readonly(s)

    fake = FakeLLMProvider([draft("SELECT 1 AS one"), "1.", draft("SELECT 2 AS two"), "2."])
    pipeline = Pipeline(llm=fake, settings=settings, connect=counting_connect)
    pipeline.ask("one?")
    pipeline.ask("two?")
    # 3 = the schema read once (first question only) + one per question while its SQL runs.
    # (Phase 1 kept one connection open for the whole question, LLM calls included.)
    assert connections == 3
    # Both SQL prompts carry the same schema text, read only the first time.
    assert (
        fake.calls[0].prompt.split("<question>")[0] == fake.calls[2].prompt.split("<question>")[0]
    )


def test_every_question_is_audit_logged(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline, _ = make_pipeline(
        settings,
        draft("SELECT count(*) AS n FROM products"),
        "There are 77 products.",
        *[draft("DROP TABLE products")] * 3,
    )
    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        pipeline.ask("How many products?")
        with pytest.raises(UnsafeSqlError):
            pipeline.ask("Drop products")

    ok, rejected = [r for r in caplog.records if r.name == "querymind.audit"]
    assert ok.question == "How many products?"  # type: ignore[attr-defined]
    assert ok.outcome == "ok"  # type: ignore[attr-defined]
    assert ok.row_count == 1  # type: ignore[attr-defined]
    assert rejected.outcome == "UnsafeSqlError"  # type: ignore[attr-defined]
    assert rejected.sql == "DROP TABLE products"  # type: ignore[attr-defined]


def test_summary_can_be_skipped(settings: Settings) -> None:
    # The eval scores results only, so it skips the summary call (half the LLM tokens).
    pipeline, fake = make_pipeline(settings, draft("SELECT count(*) AS n FROM customers"))
    result = ask(pipeline, "How many customers?", summarize_answer=False)
    assert result.answer.text == "n: 91"
    assert result.answer.from_llm is False
    assert len(fake.calls) == 1  # only the SQL call


def test_an_ambiguous_question_gets_a_question_back(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    clarification = Clarification(
        question="Best by what?", options=["by total revenue", "by number of orders"]
    )
    reply_draft = SqlDraft(sql="", explanation="", tables_used=[], clarification=clarification)
    pipeline, fake = make_pipeline(settings, reply_draft)
    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        reply = pipeline.ask("Who are our best customers?")
    assert reply == ClarificationRequest(
        question="Who are our best customers?", clarification=clarification
    )
    assert len(fake.calls) == 1  # no SQL ran, no summary
    (audit,) = [r for r in caplog.records if r.name == "querymind.audit"]
    assert audit.outcome == "clarification"  # type: ignore[attr-defined]


def test_a_change_request_is_refused_politely(settings: Settings) -> None:
    refusal = "I can only read data, not change it."
    pipeline, _ = make_pipeline(
        settings, SqlDraft(sql="", explanation="", tables_used=[], refusal=refusal)
    )
    assert pipeline.ask("Delete all orders") == Refusal(
        question="Delete all orders", message=refusal
    )
    with connect_readonly(settings) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (830,)
