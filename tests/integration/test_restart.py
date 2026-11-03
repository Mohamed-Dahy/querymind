"""The U3 milestone in one test: a conversation survives a restart.

A scripted LLM, with everything else real: the API, the agent, Postgres and the store. Two
`Pipeline` objects stand in for a restart: a new one has an empty memory, just like a new process.
"""

from collections.abc import Iterator
from contextlib import suppress
from functools import partial
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import Clarification, SqlDraft
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.llm.fake import FakeLLMProvider, FakeResponse
from querymind.store.conversations import ConversationStore, load_history
from querymind.store.errors import ConversationNotFoundError

pytestmark = pytest.mark.integration

COUNT_PER_COUNTRY = "SELECT country, COUNT(*) AS customers FROM customers GROUP BY country"
ONLY_GERMANY = f"{COUNT_PER_COUNTRY} HAVING country = 'Germany'"


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Counts customers.", tables_used=["customers"])


@pytest.fixture
def store(settings: Settings) -> ConversationStore:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: history is off (see .env.example)")
    return ConversationStore(settings)


@pytest.fixture
def conversation(store: ConversationStore) -> Iterator[UUID]:
    conversation_id = uuid4()
    yield conversation_id
    with suppress(ConversationNotFoundError):
        store.delete(conversation_id)


def started_api(
    settings: Settings, store: ConversationStore, *responses: FakeResponse
) -> tuple[TestClient, FakeLLMProvider]:
    """The API as it starts up in production, with a brand new, empty pipeline."""
    llm = FakeLLMProvider(responses)
    pipeline = Pipeline(
        llm=llm,
        settings=settings.model_copy(update={"schema_retrieval": "off"}),
        history_loader=partial(load_history, store),
    )
    return TestClient(create_app(pipeline=pipeline, store=store)), llm


def test_a_follow_up_works_after_a_restart(
    settings: Settings, store: ConversationStore, conversation: UUID
) -> None:
    first_api, _ = started_api(settings, store, draft(COUNT_PER_COUNTRY), "Customers per country.")
    with first_api as api:
        first = api.post(
            "/ask/stream",
            json={"question": "Customers per country?", "conversation_id": str(conversation)},
        )
    assert first.status_code == 200

    # "Restart": a new pipeline, whose memory knows nothing.
    second_api, llm = started_api(settings, store, draft(ONLY_GERMANY), "Germany has 11 customers.")
    with second_api as api:
        second = api.post(
            "/ask",
            json={"question": "Only Germany", "conversation_id": str(conversation)},
        )

    assert second.status_code == 200
    assert second.json()["rows"] == [["Germany", 11]]
    prompt = llm.calls[0].prompt
    assert "Question: Customers per country?" in prompt
    assert COUNT_PER_COUNTRY in prompt
    assert "21 rows, columns: country, customers" in prompt
    assert "Argentina" not in prompt  # the rows themselves never go back to the LLM (§8.5)


def test_both_turns_are_saved_in_order_with_no_rows(
    settings: Settings, store: ConversationStore, conversation: UUID
) -> None:
    api_one, _ = started_api(settings, store, draft(COUNT_PER_COUNTRY), "Customers per country.")
    with api_one as api:
        api.post(
            "/ask/stream",
            json={"question": "Customers per country?", "conversation_id": str(conversation)},
        )
    api_two, _ = started_api(settings, store, draft(ONLY_GERMANY), "Germany has 11 customers.")
    with api_two as api:
        api.post("/ask", json={"question": "Only Germany", "conversation_id": str(conversation)})

    saved = store.get(conversation)

    assert saved.title == "Customers per country?"
    assert [turn.question for turn in saved.saved_turns] == [
        "Customers per country?",
        "Only Germany",
    ]
    first, second = saved.saved_turns
    assert first.reply["row_count"] == 21
    assert "rows" not in first.reply and "rows" not in second.reply
    assert first.sql is not None and "GROUP BY country" in first.sql
    # The stream's steps are kept, so the rail can draw at once; POST /ask has none.
    assert {event["type"] for event in first.trace} >= {"step", "sql", "validated", "checked"}
    assert second.trace == []
    assert "rows" not in str(first.trace)


def test_a_clarification_is_remembered_after_a_restart(
    settings: Settings, store: ConversationStore, conversation: UUID
) -> None:
    asked = SqlDraft(
        clarification=Clarification(question="By what?", options=["by orders", "by revenue"]),
        sql="",
        explanation="",
        tables_used=[],
    )
    api_one, _ = started_api(settings, store, asked)
    with api_one as api:
        api.post(
            "/ask/stream",
            json={"question": "Who is best?", "conversation_id": str(conversation)},
        )

    api_two, llm = started_api(settings, store, draft(COUNT_PER_COUNTRY), "Customers.")
    with api_two as api:
        api.post("/ask", json={"question": "by orders", "conversation_id": str(conversation)})

    assert "You asked back: By what? Options: by orders; by revenue" in llm.calls[0].prompt
