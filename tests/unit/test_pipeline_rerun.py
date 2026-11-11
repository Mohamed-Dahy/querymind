"""Pipeline.rerun (U3.3): stored SQL is validated again, is audited, and uses no LLM."""

import logging

import pytest

from querymind.agent import pipeline as pipeline_module
from querymind.db.errors import QueryExecutionError
from querymind.safety.errors import UnsafeSqlError
from tests.unit.test_conversation import SCHEMA, TOP_CUSTOMERS, StubExecutor, pipeline_with

GOOD = "SELECT company_name, revenue FROM customers"


@pytest.fixture
def executor(monkeypatch: pytest.MonkeyPatch) -> StubExecutor:
    stub = StubExecutor()
    monkeypatch.setattr(pipeline_module, "execute_query", stub)
    monkeypatch.setattr(pipeline_module, "read_schema", lambda conn: SCHEMA)
    return stub


def audit_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "querymind.audit"]


def test_it_returns_fresh_rows_and_calls_no_llm(executor: StubExecutor) -> None:
    pipeline, llm = pipeline_with()

    result = pipeline.rerun("Top customers", GOOD, conversation_id="c1")

    assert result == TOP_CUSTOMERS
    assert llm.calls == []


def test_it_runs_the_validated_sql_with_the_row_limit(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with()

    pipeline.rerun("Top customers", GOOD)

    # The validator adds a LIMIT one above max_rows, which is how truncation is noticed.
    assert "LIMIT" in executor.queries[0].sql.upper()
    assert executor.queries[0].sql != GOOD


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "SELECT * FROM querymind_app.turns",
        "SELECT * FROM pg_catalog.pg_user",
        "SELECT pg_sleep(100)",
        "SELECT 1; DROP TABLE customers",
        "SELECT * FROM not_an_allowed_table",
    ],
)
def test_stored_sql_is_never_trusted(executor: StubExecutor, sql: str) -> None:
    # Someone with access to the app's database could have changed what is stored.
    pipeline, _ = pipeline_with()

    with pytest.raises(UnsafeSqlError):
        pipeline.rerun("Top customers", sql)

    assert executor.queries == []


def test_a_rerun_writes_an_audit_line(
    executor: StubExecutor, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline, _ = pipeline_with()

    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        pipeline.rerun("Top customers", GOOD, conversation_id="c1")

    [line] = audit_lines(caplog)
    assert getattr(line, "outcome", None) == "rerun"
    assert getattr(line, "question", None) == "Top customers"
    assert getattr(line, "conversation_id", None) == "c1"
    assert "customers" in str(getattr(line, "sql", ""))
    assert getattr(line, "row_count", None) == 2
    assert getattr(line, "sql_attempts", None) == 1


def test_a_refused_rerun_is_audited_with_the_sql_it_tried(
    executor: StubExecutor, caplog: pytest.LogCaptureFixture
) -> None:
    pipeline, _ = pipeline_with()

    with (
        caplog.at_level(logging.INFO, logger="querymind.audit"),
        pytest.raises(UnsafeSqlError),
    ):
        pipeline.rerun("Top customers", "DROP TABLE customers")

    [line] = audit_lines(caplog)
    assert getattr(line, "outcome", None) == "UnsafeSqlError"
    assert getattr(line, "sql", None) == "DROP TABLE customers"


def test_a_failing_query_is_audited_and_raised(
    executor: StubExecutor, caplog: pytest.LogCaptureFixture
) -> None:
    executor.replies.append(QueryExecutionError("It failed.", detail="column is gone"))
    pipeline, _ = pipeline_with()

    with (
        caplog.at_level(logging.INFO, logger="querymind.audit"),
        pytest.raises(QueryExecutionError),
    ):
        pipeline.rerun("Top customers", GOOD)

    [line] = audit_lines(caplog)
    assert getattr(line, "outcome", None) == "QueryExecutionError"
    assert "customers" in str(getattr(line, "sql", ""))


def test_a_rerun_leaves_the_conversation_s_memory_alone(executor: StubExecutor) -> None:
    pipeline, _ = pipeline_with()

    pipeline.rerun("Top customers", GOOD, conversation_id="c1")

    assert "c1" not in pipeline._conversations
