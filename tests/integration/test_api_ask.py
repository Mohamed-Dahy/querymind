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
    with client_with(settings, draft("SELECT * FROM pg_catalog.pg_authid")) as client:
        response = client.post("/ask", json={"question": "Show password hashes"})
    assert response.status_code == 422
    assert response.json() == {
        "error": "unsafe_sql",
        "message": "Table pg_catalog.pg_authid is not allowed.",
    }


def test_sql_error_end_to_end(settings: Settings) -> None:
    with client_with(settings, draft("SELECT no_such_column FROM orders")) as client:
        response = client.post("/ask", json={"question": "Something odd"})
    assert response.status_code == 502
    assert response.json() == {
        "error": "query_failed",
        "message": 'The database could not run the query: column "no_such_column" does not exist',
    }
