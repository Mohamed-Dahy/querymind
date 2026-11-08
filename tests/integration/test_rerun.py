"""Re-running a saved answer on the real database: fresh rows, and stored SQL is never trusted.

A scripted LLM; the API, agent, Postgres and store are real. These tests change a stored turn, and
one adds a row to `shippers` for a moment, as the admin, and always removes it.
"""

import logging
from collections.abc import Iterator
from contextlib import suppress
from functools import partial
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.llm.fake import FakeLLMProvider
from querymind.store.conversations import ConversationStore, Owner, load_history
from querymind.store.errors import ConversationNotFoundError
from tests.auth_helpers import signed_in, user_for

pytestmark = pytest.mark.integration

COUNT_SHIPPERS = "SELECT count(*) AS shippers FROM shippers"
SCRATCH_SHIPPER = 32000  # shipper_id is a smallint


@pytest.fixture
def store(settings: Settings) -> ConversationStore:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: history is off (see .env.example)")
    return ConversationStore(settings)


@pytest.fixture
def conversation(store: ConversationStore, owner: Owner) -> Iterator[UUID]:
    conversation_id = uuid4()
    yield conversation_id
    with suppress(ConversationNotFoundError):
        store.delete(conversation_id, owner=owner)


@pytest.fixture
def scratch_shipper(settings: Settings) -> Iterator[None]:
    yield
    with psycopg.connect(settings.admin_conninfo()) as admin:
        admin.execute("DELETE FROM shippers WHERE shipper_id = %s", (SCRATCH_SHIPPER,))


@pytest.fixture
def api(settings: Settings, store: ConversationStore, owner: Owner) -> Iterator[TestClient]:
    llm = FakeLLMProvider(
        [
            SqlDraft(sql=COUNT_SHIPPERS, explanation="Counts.", tables_used=["shippers"]),
            "There are 6 shippers.",
        ]
    )
    pipeline = Pipeline(
        llm=llm,
        settings=settings.model_copy(update={"schema_retrieval": "off"}),
        history_loader=partial(load_history, store),
    )
    app = signed_in(create_app(pipeline=pipeline, store=store), user_for(owner))
    with TestClient(app) as client:
        yield client


def ask(api: TestClient, conversation: UUID) -> None:
    response = api.post(
        "/ask", json={"question": "How many shippers?", "conversation_id": str(conversation)}
    )
    assert response.status_code == 200


def change_stored_sql(settings: Settings, conversation: UUID, sql: str) -> None:
    """What someone with the app's database password could do."""
    with connect_app(settings) as conn:
        conn.execute(
            "UPDATE querymind_app.turns SET sql = %s WHERE conversation_id = %s",
            (sql, conversation),
        )


def test_the_saved_turn_has_no_rows_and_a_rerun_brings_fresh_ones(
    api: TestClient, conversation: UUID
) -> None:
    ask(api, conversation)

    saved = api.get(f"/conversations/{conversation}").json()["saved_turns"][0]
    assert saved["reply"]["rows"] == []
    assert saved["reply"]["row_count"] == 1

    fresh = api.post(f"/conversations/{conversation}/turns/1/rerun").json()
    assert fresh["columns"] == ["shippers"]
    assert fresh["row_count"] == 1
    assert isinstance(fresh["rows"][0][0], int)


def test_a_rerun_sees_data_that_changed_since_the_answer(
    api: TestClient, conversation: UUID, settings: Settings, scratch_shipper: None
) -> None:
    ask(api, conversation)
    before = api.post(f"/conversations/{conversation}/turns/1/rerun").json()["rows"][0][0]

    with psycopg.connect(settings.admin_conninfo()) as admin:
        admin.execute(
            "INSERT INTO shippers (shipper_id, company_name) VALUES (%s, 'Scratch')",
            (SCRATCH_SHIPPER,),
        )

    after = api.post(f"/conversations/{conversation}/turns/1/rerun").json()["rows"][0][0]
    assert after == before + 1


@pytest.mark.parametrize(
    "stored",
    [
        "DROP TABLE shippers",
        "DELETE FROM shippers",
        "SELECT * FROM querymind_app.conversations",
        "SELECT usename, passwd FROM pg_shadow",
        "SELECT pg_sleep(30)",
    ],
)
def test_stored_sql_changed_to_something_dangerous_is_refused(
    api: TestClient, conversation: UUID, settings: Settings, stored: str
) -> None:
    ask(api, conversation)
    change_stored_sql(settings, conversation, stored)

    response = api.post(f"/conversations/{conversation}/turns/1/rerun")

    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_sql"
    # And nothing happened to the table.
    with psycopg.connect(settings.admin_conninfo()) as admin:
        assert admin.execute("SELECT count(*) FROM shippers").fetchone() is not None


def test_each_rerun_writes_an_audit_line(
    api: TestClient, conversation: UUID, caplog: pytest.LogCaptureFixture
) -> None:
    ask(api, conversation)

    with caplog.at_level(logging.INFO, logger="querymind.audit"):
        api.post(f"/conversations/{conversation}/turns/1/rerun")
        api.post(f"/conversations/{conversation}/turns/1/rerun")

    lines = [r for r in caplog.records if r.name == "querymind.audit"]
    assert [getattr(line, "outcome", None) for line in lines] == ["rerun", "rerun"]
    assert all(getattr(line, "conversation_id", None) == str(conversation) for line in lines)


def test_a_deleted_conversation_is_gone_from_the_store_and_the_agent_s_memory(
    api: TestClient, conversation: UUID
) -> None:
    ask(api, conversation)

    assert api.delete(f"/conversations/{conversation}").status_code == 204

    assert api.get(f"/conversations/{conversation}").status_code == 404
    assert api.post(f"/conversations/{conversation}/turns/1/rerun").status_code == 404
    assert api.delete(f"/conversations/{conversation}").status_code == 404


def test_rename_pin_and_list_through_the_api(api: TestClient, conversation: UUID) -> None:
    ask(api, conversation)

    renamed = api.patch(f"/conversations/{conversation}", json={"title": "Weekly", "pinned": True})
    assert renamed.json()["title"] == "Weekly"
    assert renamed.json()["pinned"] is True

    top = api.get("/conversations", params={"limit": 1}).json()
    assert top["conversations"][0]["id"] == str(conversation)
    found = api.get("/conversations", params={"q": "How many shippers"}).json()
    assert str(conversation) in [c["id"] for c in found["conversations"]]
