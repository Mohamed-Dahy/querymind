"""The whole Phase 1 pipeline on the real database, with a scripted fake LLM (no network).

Real: schema introspection, validator, executor, Postgres. Fake: the two LLM calls (SQL, answer).
"""

import logging

import psycopg
import pytest

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import QueryExecutionError
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError

pytestmark = pytest.mark.integration


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Test query.", tables_used=[])


def make_pipeline(settings: Settings, *responses: object) -> tuple[Pipeline, FakeLLMProvider]:
    fake = FakeLLMProvider(responses)  # type: ignore[arg-type]
    return Pipeline(llm=fake, settings=settings), fake


def test_question_to_answer(settings: Settings) -> None:
    pipeline, fake = make_pipeline(
        settings,
        draft("SELECT count(*) AS customer_count FROM customers"),
        "There are 91 customers.",
    )
    result = pipeline.ask("How many customers are there?")

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
    result = pipeline.ask("How many customers are there?")
    assert result.answer.from_llm is False
    assert result.answer.text == "customer_count: 91"


def test_unsafe_sql_is_rejected_before_it_runs(settings: Settings) -> None:
    pipeline, fake = make_pipeline(settings, draft("DELETE FROM orders"))
    with pytest.raises(UnsafeSqlError, match="Only SELECT"):
        pipeline.ask("Delete all orders")
    assert len(fake.calls) == 1  # no summary call either
    with connect_readonly(settings) as conn:
        assert conn.execute("SELECT count(*) FROM orders").fetchone() == (830,)


def test_database_errors_propagate_cleanly(settings: Settings) -> None:
    pipeline, _ = make_pipeline(settings, draft("SELECT no_such_column FROM orders"))
    with pytest.raises(QueryExecutionError, match="does not exist"):
        pipeline.ask("Something odd")


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
    assert connections == 2  # one connection per question
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
        draft("DROP TABLE products"),
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
