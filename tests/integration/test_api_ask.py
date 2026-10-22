"""POST /ask end to end: HTTP → real Pipeline → real Postgres, with a scripted fake LLM."""

import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.llm.fake import FakeLLMProvider, FakeResponse

pytestmark = pytest.mark.integration


def client_with(settings: Settings, *responses: FakeResponse) -> TestClient:
    pipeline = Pipeline(llm=FakeLLMProvider(responses), settings=settings)
    return TestClient(create_app(pipeline=pipeline))


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Test query.", tables_used=[])


def test_ask_end_to_end(settings: Settings) -> None:
    with client_with(
        settings,
        draft("SELECT product_name, unit_price FROM products ORDER BY unit_price DESC LIMIT 3"),
        "The most expensive product is Côte de Blaye at 263.5.",
    ) as client:
        response = client.post("/ask", json={"question": "Top 3 most expensive products?"})

    assert response.status_code == 200
    body = response.json()
    assert body["columns"] == ["product_name", "unit_price"]
    assert body["rows"][0] == ["Côte de Blaye", 263.5]
    assert body["row_count"] == 3
    assert body["answer"] == "The most expensive product is Côte de Blaye at 263.5."
    assert body["answer_from_llm"] is True
    assert body["sql"].endswith("LIMIT 3")


def test_unsafe_sql_end_to_end(settings: Settings) -> None:
    with client_with(settings, *[draft("SELECT * FROM pg_catalog.pg_authid")] * 3) as client:
        response = client.post("/ask", json={"question": "Show password hashes"})
    assert response.status_code == 422
    assert response.json() == {
        "error": "unsafe_sql",
        "message": "Table pg_catalog.pg_authid is not allowed.",
    }


def test_sql_error_end_to_end(settings: Settings) -> None:
    with client_with(settings, *[draft("SELECT no_such_column FROM orders")] * 3) as client:
        response = client.post("/ask", json={"question": "Something odd"})
    assert response.status_code == 502
    assert response.json() == {
        "error": "query_failed",
        "message": 'The database could not run the query: column "no_such_column" does not exist',
    }


REVENUE = "sum(d.unit_price * d.quantity * (1 - d.discount))"
FOUR_TURNS = [
    (
        "Top 5 customers by revenue",
        f"SELECT c.company_name, {REVENUE} AS revenue FROM customers AS c "
        "JOIN orders AS o ON o.customer_id = c.customer_id "
        "JOIN order_details AS d ON d.order_id = o.order_id "
        "GROUP BY c.company_name ORDER BY revenue DESC LIMIT 5",
    ),
    (
        "Only for 1997",
        f"SELECT c.company_name, {REVENUE} AS revenue FROM customers AS c "
        "JOIN orders AS o ON o.customer_id = c.customer_id "
        "JOIN order_details AS d ON d.order_id = o.order_id "
        "WHERE extract(year FROM o.order_date) = 1997 "
        "GROUP BY c.company_name ORDER BY revenue DESC LIMIT 5",
    ),
    (
        "Break that down by country",
        f"SELECT c.country, {REVENUE} AS revenue FROM customers AS c "
        "JOIN orders AS o ON o.customer_id = c.customer_id "
        "JOIN order_details AS d ON d.order_id = o.order_id "
        "WHERE extract(year FROM o.order_date) = 1997 "
        "GROUP BY c.country ORDER BY revenue DESC",
    ),
    (
        "Show the top one's orders",
        "SELECT o.order_id, o.order_date FROM orders AS o "
        "JOIN customers AS c ON c.customer_id = o.customer_id "
        "WHERE c.country = 'USA' AND extract(year FROM o.order_date) = 1997 "
        "ORDER BY o.order_date",
    ),
]


def test_a_four_turn_conversation(settings: Settings) -> None:
    # Each turn: the LLM's SQL, then its summary (no numbers, so it passes the grounding check).
    responses: list[FakeResponse] = []
    for _, sql in FOUR_TURNS:
        responses += [draft(sql), "Here is what the data shows."]
    fake = FakeLLMProvider(responses)
    pipeline = Pipeline(llm=fake, settings=settings)

    conversation = None
    sent_sql = []
    with TestClient(create_app(pipeline=pipeline)) as client:
        for question, _ in FOUR_TURNS:
            body = {"question": question, "conversation_id": conversation}
            response = client.post("/ask", json=body)
            assert response.status_code == 200, response.json()
            reply = response.json()
            assert reply["kind"] == "answer" and reply["row_count"] > 0
            conversation = conversation or reply["conversation_id"]
            assert reply["conversation_id"] == conversation  # one conversation throughout
            sent_sql.append(reply["sql"])

    sql_prompts = [call.prompt for call in fake.calls if call.schema is SqlDraft]
    assert "<history>" not in sql_prompts[0]
    last = sql_prompts[3]
    for question, sql in zip([q for q, _ in FOUR_TURNS[:3]], sent_sql[:3], strict=True):
        assert f"Question: {question}\nSQL: {sql}" in last  # every earlier turn, in order
