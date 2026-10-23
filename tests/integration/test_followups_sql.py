"""Every turn of the follow-up conversations (3.6) runs on the real database and returns rows,
so the history evals/followups.py builds from them is a real one."""

import psycopg
import pytest

from evals.followups import Conversation, load_followups
from querymind.config import Settings
from querymind.db.executor import execute_query
from querymind.db.schema import introspect_schema
from querymind.safety.sql_validator import validate_sql

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("conversation", load_followups().conversations, ids=lambda c: c.id)
def test_every_turn_runs_and_returns_rows(
    ro_conn: psycopg.Connection, settings: Settings, conversation: Conversation
) -> None:
    tables = introspect_schema(ro_conn).table_names
    for turn in conversation.turns:
        query = validate_sql(turn.sql, allowed_tables=tables, max_rows=settings.max_rows)
        result = execute_query(ro_conn, query, timeout_seconds=settings.query_timeout_seconds)
        assert result.row_count > 0, (conversation.id, turn.question)
