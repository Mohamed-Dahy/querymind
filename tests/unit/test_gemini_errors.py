"""Gemini failures become LLMProviderError ("provider down": the eval retries, then skips)."""

import httpx
import pytest
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr

from querymind.agent.schemas import Clarification
from querymind.llm import LLMProviderError
from querymind.llm.gemini_provider import GeminiProvider

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
