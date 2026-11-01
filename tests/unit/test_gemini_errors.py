"""Gemini failures become LLMProviderError ("provider down": the eval retries, then skips),
or LLMRateLimitError when Gemini said "too many requests"."""

from collections.abc import Sequence

import httpx
import pytest
from google.genai import errors as genai_errors
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleRateLimitError
from pydantic import SecretStr

from querymind.agent.schemas import Clarification
from querymind.llm import LLMProviderError, LLMRateLimitError
from querymind.llm.gemini_provider import GeminiProvider, is_rate_limit, retry_after_seconds

REQUEST = httpx.Request("POST", "https://generativelanguage.googleapis.com/")


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=REQUEST),  # no network
        httpx.ReadTimeout("timed out", request=REQUEST),
    ],
)
def test_network_failures_are_provider_errors(
    error: Exception, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(ChatGoogleGenerativeAI, "_generate", fail)
    provider = GeminiProvider(model="gemini-3.5-flash-lite", api_key=SecretStr("test"))
    with pytest.raises(LLMProviderError):
        provider.generate_text("q")
    with pytest.raises(LLMProviderError):
        provider.generate_structured("q", Clarification)


def client_error(code: int, details: Sequence[object] = ()) -> genai_errors.ClientError:
    """The SDK's error for an HTTP error response, with Google's JSON body."""
    status = "RESOURCE_EXHAUSTED" if code == 429 else "INVALID_ARGUMENT"
    body = {"error": {"code": code, "message": "...", "status": status, "details": list(details)}}
    return genai_errors.ClientError(code, body)


RETRY_INFO = [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": []},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17.5s"},
]


def wrapped(cause: Exception) -> GoogleRateLimitError:
    """What LangChain raises for a 429 (chat_models._handle_client_error)."""
    error = GoogleRateLimitError("Error calling model 'gemini' (RESOURCE_EXHAUSTED)")
    error.__cause__ = cause
    return error


def test_a_429_is_a_rate_limit_directly_or_wrapped_by_langchain() -> None:
    assert is_rate_limit(client_error(429))
    assert is_rate_limit(wrapped(client_error(429)))
    assert not is_rate_limit(client_error(400))
    assert not is_rate_limit(httpx.ConnectError("down", request=REQUEST))


def test_the_wait_comes_from_retry_info_in_the_body() -> None:
    assert retry_after_seconds(wrapped(client_error(429, RETRY_INFO))) == 17.5


@pytest.mark.parametrize(
    "error",
    [
        client_error(429),  # no details
        client_error(429, ["not a dict"]),
        client_error(429, [{"retryDelay": "soon"}]),
        genai_errors.ClientError(429, "not json"),
        httpx.ConnectError("down", request=REQUEST),
    ],
)
def test_no_usable_wait_is_none(error: Exception) -> None:
    assert retry_after_seconds(error) is None


def test_the_provider_raises_a_rate_limit_error_with_the_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise wrapped(client_error(429, RETRY_INFO))

    monkeypatch.setattr(ChatGoogleGenerativeAI, "_generate", fail)
    provider = GeminiProvider(model="gemini-3.5-flash-lite", api_key=SecretStr("test"))
    with pytest.raises(LLMRateLimitError) as info:
        provider.generate_structured("q", Clarification)
    assert info.value.retry_after_seconds == 17.5
