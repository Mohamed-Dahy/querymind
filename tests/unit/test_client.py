"""QueryMindClient against a fake HTTP server (httpx.MockTransport): no real network."""

import json
from collections.abc import Callable

import httpx
import pytest

from querymind.api.schemas import AskResponse, ClarificationResponse, RefusalResponse
from querymind.client import ApiError, ClientSettings, QueryMindClient

ANSWER = {
    "kind": "answer",
    "question": "How many customers?",
    "answer": "There are 91 customers.",
    "answer_from_llm": True,
    "sql": "SELECT count(*) FROM customers LIMIT 1001",
    "explanation": "Counts customers.",
    "columns": ["count"],
    "rows": [[91]],
    "row_count": 1,
    "truncated": False,
}


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> QueryMindClient:
    return QueryMindClient("http://api.test", transport=httpx.MockTransport(handler))


def test_ask_posts_the_question_and_parses_the_answer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=ANSWER)

    response = client_for(handler).ask("How many customers?")
    assert isinstance(response, AskResponse)
    assert response.answer == "There are 91 customers."
    assert response.rows == [[91]]
    [request] = seen
    assert (request.method, request.url.path) == ("POST", "/ask")
    assert json.loads(request.content) == {"question": "How many customers?"}


def test_health() -> None:
    client = client_for(lambda _: httpx.Response(200, json={"status": "ok", "version": "0.1.0"}))
    assert client.health().version == "0.1.0"


def test_api_errors_keep_their_code_and_message() -> None:
    body = {"error": "unsafe_sql", "message": "Only SELECT queries are allowed."}
    client = client_for(lambda _: httpx.Response(422, json=body))
    with pytest.raises(ApiError) as info:
        client.ask("Delete everything")
    assert (info.value.code, info.value.message, info.value.status) == (
        "unsafe_sql",
        "Only SELECT queries are allowed.",
        422,
    )


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="<html>Bad Gateway</html>"),  # e.g. a proxy's error page
        httpx.Response(500, json={"unexpected": "shape"}),
    ],
)
def test_non_api_errors_become_bad_response(response: httpx.Response) -> None:
    with pytest.raises(ApiError) as info:
        client_for(lambda _: response).ask("q")
    assert info.value.code == "bad_response"
    assert info.value.status == response.status_code


def test_unexpected_success_body_is_bad_response() -> None:
    with pytest.raises(ApiError, match="unexpected response") as info:
        client_for(lambda _: httpx.Response(200, json={"hello": "world"})).ask("q")
    assert info.value.code == "bad_response"


def test_unreachable_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(ApiError, match="Is it running") as info:
        client_for(handler).ask("q")
    assert info.value.code == "api_unreachable"


def test_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(ApiError) as info:
        client_for(handler).ask("q")
    assert info.value.code == "api_timeout"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_URL", raising=False)
    monkeypatch.delenv("API_TIMEOUT_SECONDS", raising=False)
    settings = ClientSettings(_env_file=None)
    assert settings.api_url == "http://127.0.0.1:8000"
    assert settings.api_timeout_seconds == 120.0


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_URL", "http://api.internal:9000")
    assert ClientSettings(_env_file=None).api_url == "http://api.internal:9000"


def test_a_clarification_is_parsed_by_its_kind() -> None:
    body = {
        "kind": "clarification",
        "question": "Best customers?",
        "clarifying_question": "Best by what?",
        "options": ["by total revenue", "by number of orders"],
    }
    response = client_for(lambda _: httpx.Response(200, json=body)).ask("Best customers?")
    assert isinstance(response, ClarificationResponse)
    assert response.options == ["by total revenue", "by number of orders"]


def test_a_refusal_is_parsed_by_its_kind() -> None:
    body = {"kind": "refusal", "question": "Delete orders", "message": "I can only read data."}
    response = client_for(lambda _: httpx.Response(200, json=body)).ask("Delete orders")
    assert response == RefusalResponse(question="Delete orders", message="I can only read data.")


def test_an_unknown_kind_is_a_bad_response() -> None:
    body = {"kind": "poem", "question": "?"}
    with pytest.raises(ApiError) as info:
        client_for(lambda _: httpx.Response(200, json=body)).ask("?")
    assert info.value.code == "bad_response"
