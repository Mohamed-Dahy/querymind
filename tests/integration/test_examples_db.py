"""Every few-shot example (3.5) runs on the real database, and none answers a golden question.

Like the golden references: each example's SQL must pass the validator and run as analyst_ro,
and return rows (an example that returns nothing teaches nothing). And no example may return
the same rows from the same tables as a golden question: that would be a leak in other words.
"""

from pathlib import Path

import psycopg
import pytest

from evals.compare import compare_results
from evals.golden_set import GOLDEN_DIR, load_golden_set
from querymind.agent.schemas import FewShotExample
from querymind.config import Settings
from querymind.db.executor import QueryResult, execute_query
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.examples import load_all_examples

pytestmark = pytest.mark.integration

LAYERS = Path(__file__).parents[2] / "semantic_layer"
EXAMPLES = load_all_examples([LAYERS / "northwind.yaml", LAYERS / "chinook.yaml"])
GOLDEN = [
    q
    for name in ("northwind.yaml", "northwind_business.yaml", "chinook.yaml")
    for q in load_golden_set(GOLDEN_DIR / name).questions
]


def run(
    conn: psycopg.Connection, sql: str, settings: Settings
) -> tuple[QueryResult, frozenset[str]]:
    tables = frozenset(
        row[0]
        for row in conn.execute(
            "SELECT table_name::text FROM information_schema.tables WHERE table_schema = 'public'"
        )
    )
    query = validate_sql(sql, allowed_tables=tables, max_rows=settings.max_rows)
    return execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds), query.tables


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda e: e.id)
def test_every_example_runs_and_returns_rows(
    ro_conn: psycopg.Connection, settings: Settings, example: FewShotExample
) -> None:
    result, _ = run(ro_conn, example.sql, settings)
    assert result.row_count > 0
    assert not result.truncated


def test_no_example_answers_a_golden_question(
    ro_conn: psycopg.Connection, settings: Settings
) -> None:
    golden = [(q.id, *run(ro_conn, q.sql, settings)) for q in GOLDEN]
    leaks = []
    for example in EXAMPLES:
        result, tables = run(ro_conn, example.sql, settings)
        for golden_id, expected, golden_tables in golden:
            same = compare_results(expected=expected, actual=result, ordered=False)
            if tables == golden_tables and same.match:
                leaks.append((example.id, golden_id))
    assert leaks == []
