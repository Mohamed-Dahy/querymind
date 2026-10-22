"""A small Python client for the QueryMind HTTP API (used by the Streamlit UI).

    client = QueryMindClient("http://127.0.0.1:8000")
    response = client.ask("How many customers are there?")
    response.answer, response.sql, response.rows
    client.ask("And in Germany?", conversation_id=response.conversation_id)  # a follow-up

Every failure becomes an `ApiError` with a stable `code` and a message for people, whether the
API answered with an error, timed out, or isn't running at all.
"""

from uuid import UUID

import httpx
from pydantic import BaseModel, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from querymind.api.schemas import (
    AskReply,
    AskResponse,
    ClarificationResponse,
    HealthResponse,
    RefusalResponse,
)


class ClientSettings(BaseSettings):
    """Where the API is. Read from API_URL / API_TIMEOUT_SECONDS (env or .env)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    api_url: str = "http://127.0.0.1:8000"
    # A question makes two LLM calls plus a query; with rate-limit retries that can take a while.
    api_timeout_seconds: float = 120.0


class ApiError(Exception):
    """The API call failed. `code` is stable (e.g. "unsafe_sql"); `message` is for people."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class QueryMindClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """`transport` lets tests answer requests without a real server."""
        self._http = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)
        self.base_url = base_url

    def ask(
        self, question: str, *, conversation_id: UUID | None = None
    ) -> AskResponse | ClarificationResponse | RefusalResponse:
        """Ask a question: an answer, a question back, or a refusal. Raises ApiError on failure.

        Pass the `conversation_id` of an earlier reply to follow up on it.
        """
        body = {
            "question": question,
            "conversation_id": str(conversation_id) if conversation_id else None,
        }
        response = self._request("POST", "/ask", json=body)
        try:
            # `kind` in the body says which of the three it is (a "discriminated union").
            return _ASK_REPLY.validate_json(response.content)
        except ValidationError as exc:
            raise ApiError("bad_response", "The API sent an unexpected response.") from exc

    def health(self) -> HealthResponse:
        """Check the API is up. Raises ApiError if it isn't."""
        return self._parse(self._request("GET", "/health"), HealthResponse)

    def _request(
        self, method: str, path: str, *, json: dict[str, str | None] | None = None
    ) -> httpx.Response:
        try:
            response = self._http.request(method, path, json=json)
        except httpx.TimeoutException as exc:
            raise ApiError("api_timeout", "The API took too long to answer.") from exc
        except httpx.TransportError as exc:
            raise ApiError(
                "api_unreachable",
                f"Can't reach the QueryMind API at {self.base_url}. Is it running?",
            ) from exc
        if response.is_error:
            raise _error_from(response)
        return response

    def _parse[T: BaseModel](self, response: httpx.Response, model: type[T]) -> T:
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            raise ApiError("bad_response", "The API sent an unexpected response.") from exc


_ASK_REPLY: TypeAdapter[AskResponse | ClarificationResponse | RefusalResponse] = TypeAdapter(
    AskReply
)


def _error_from(response: httpx.Response) -> ApiError:
    """Our API always sends {"error", "message"}; anything else (a proxy page...) is generic."""
    try:
        body = response.json()
        return ApiError(body["error"], body["message"], status=response.status_code)
    except (ValueError, KeyError, TypeError):
        return ApiError(
            "bad_response",
            f"The API answered with HTTP {response.status_code}.",
            status=response.status_code,
        )
