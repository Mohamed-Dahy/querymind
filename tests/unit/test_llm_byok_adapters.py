"""Bring your own key (5.8): the fixed provider list, the adapters it builds, and how a refused
key is told apart from an outage. No real provider is called."""

from unittest.mock import MagicMock

import anthropic
import groq
import httpx
import openai
import pytest
from google.genai import errors as genai_errors
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr

from querymind.llm import (
    LLMAuthError,
    LLMProviderError,
    LLMRateLimitError,
    anthropic_provider,
    gemini_provider,
    groq_provider,
)
from querymind.llm import openai_compatible_provider as openai_provider
from querymind.llm.anthropic_provider import AnthropicProvider
from querymind.llm.catalog import CATALOG, ProviderId, entry_for
from querymind.llm.factory import create_llm_provider_for
from querymind.llm.gemini_provider import GeminiProvider
from querymind.llm.groq_provider import GroqProvider
from querymind.llm.langchain_provider import LangChainProvider
from querymind.llm.openai_compatible_provider import OpenAICompatibleProvider
from querymind.logging_setup import REDACTED, redact

KEY = SecretStr("not-a-real-key-0123456789")
REQUEST = httpx.Request("POST", "https://example.com/v1/chat/completions")


# --- The catalog ---


def test_every_base_url_is_fixed_https_and_not_typed_by_a_user() -> None:
    for entry in CATALOG.values():
        if entry.adapter == "openai_compatible":
            assert entry.base_url is not None and entry.base_url.startswith("https://")
        else:
            assert entry.base_url is None  # their own SDK knows the address
        assert entry.keys_url.startswith("https://")


@pytest.mark.parametrize(
    ("provider", "kind"),
    [
        ("groq", GroqProvider),
        ("gemini", GeminiProvider),
        ("openai", OpenAICompatibleProvider),
        ("openrouter", OpenAICompatibleProvider),
        ("deepseek", OpenAICompatibleProvider),
        ("together", OpenAICompatibleProvider),
        ("anthropic", AnthropicProvider),
    ],
)
def test_each_provider_gets_its_adapter(provider: ProviderId, kind: type) -> None:
    built = create_llm_provider_for(provider, CATALOG[provider].example_model, KEY)

    assert isinstance(built, kind)
    assert built.name == provider  # what the audit line says answered


def test_an_openai_compatible_provider_calls_only_its_catalog_address() -> None:
    built = create_llm_provider_for("deepseek", "deepseek-chat", KEY, max_retries=0)

    chat = built._chat_model  # type: ignore[attr-defined]
    assert isinstance(chat, ChatOpenAI)
    assert chat.openai_api_base == entry_for("deepseek").base_url
    assert chat.temperature == 0.0
    assert chat.max_retries == 0


def test_openai_gets_no_temperature() -> None:
    """Its reasoning models refuse temperature=0: a good key would fail the check."""
    chat = create_llm_provider_for("openai", "gpt-4.1-mini", KEY)._chat_model  # type: ignore[attr-defined]

    assert chat.temperature is None


def test_anthropic_has_an_answer_length_cap() -> None:
    chat = create_llm_provider_for("anthropic", "claude-haiku-4-5", KEY)._chat_model  # type: ignore[attr-defined]

    assert isinstance(chat, ChatAnthropic)
    assert chat.max_tokens == anthropic_provider.MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("entry", [e for e in CATALOG.values() if e.key_prefix], ids=str)
def test_keys_with_a_known_prefix_are_hidden_in_logs(entry: object) -> None:
    prefix = entry.key_prefix  # type: ignore[attr-defined]
    key = f"{prefix}live-0123456789abcdefghij"

    assert key not in redact(f"provider said: invalid key {key}")
    assert REDACTED in redact(f"provider said: invalid key {key}")


def test_only_together_has_no_key_shape_and_says_so() -> None:
    assert [e.id for e in CATALOG.values() if e.key_prefix is None] == ["together"]


# --- A refused key is not an outage ---


def openai_error(status: int) -> openai.APIStatusError:
    kinds: dict[int, type[openai.APIStatusError]] = {
        401: openai.AuthenticationError,
        403: openai.PermissionDeniedError,
        404: openai.NotFoundError,
        429: openai.RateLimitError,
    }
    response = httpx.Response(status, request=REQUEST, headers={"retry-after": "7"})
    # The SDKs type `response` with their own copy of httpx; a plain httpx.Response works the same.
    return kinds[status]("...", response=response, body=None)  # type: ignore[arg-type]


def anthropic_error(status: int) -> anthropic.APIStatusError:
    kinds: dict[int, type[anthropic.APIStatusError]] = {
        401: anthropic.AuthenticationError,
        403: anthropic.PermissionDeniedError,
        429: anthropic.RateLimitError,
    }
    response = httpx.Response(status, request=REQUEST, headers={"retry-after": "3"})
    # The SDKs type `response` with their own copy of httpx; a plain httpx.Response works the same.
    return kinds[status]("...", response=response, body=None)  # type: ignore[arg-type]


def groq_error(status: int) -> groq.APIStatusError:
    kinds: dict[int, type[groq.APIStatusError]] = {
        401: groq.AuthenticationError,
        403: groq.PermissionDeniedError,
        429: groq.RateLimitError,
    }
    return kinds[status]("...", response=httpx.Response(status, request=REQUEST), body=None)


def gemini_error(code: int, reason: str | None = None) -> genai_errors.ClientError:
    details = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
    body = {"error": {"code": code, "message": "...", "details": details if reason else []}}
    return genai_errors.ClientError(code, body)


@pytest.mark.parametrize(
    ("is_auth_error", "error", "expected"),
    [
        (openai_provider.is_auth_error, openai_error(401), True),
        (openai_provider.is_auth_error, openai_error(403), True),
        (openai_provider.is_auth_error, openai_error(404), False),  # unknown model: not the key
        (openai_provider.is_auth_error, openai_error(429), False),
        (anthropic_provider.is_auth_error, anthropic_error(401), True),
        (anthropic_provider.is_auth_error, anthropic_error(403), True),
        (anthropic_provider.is_auth_error, anthropic_error(429), False),
        (groq_provider.is_auth_error, groq_error(401), True),
        (groq_provider.is_auth_error, groq_error(403), True),
        (groq_provider.is_auth_error, groq_error(429), False),
        # Google answers a wrong key with a 400, not a 401 (seen with a real fake key in 5.8).
        (gemini_provider.is_auth_error, gemini_error(400, "API_KEY_INVALID"), True),
        (gemini_provider.is_auth_error, gemini_error(403), True),
        (gemini_provider.is_auth_error, gemini_error(400), False),  # another bad request
        (gemini_provider.is_auth_error, gemini_error(429), False),
    ],
)
def test_which_errors_mean_a_refused_key(
    is_auth_error: object, error: Exception, expected: bool
) -> None:
    assert is_auth_error(error) is expected  # type: ignore[operator]


def test_rate_limits_keep_the_providers_wait() -> None:
    assert openai_provider.is_rate_limit(openai_error(429))
    assert openai_provider.retry_after_seconds(openai_error(429)) == 7
    assert anthropic_provider.is_rate_limit(anthropic_error(429))
    assert anthropic_provider.retry_after_seconds(anthropic_error(429)) == 3


class Answer(BaseModel):
    value: int


class RefusedKey(Exception):
    pass


def test_the_shared_adapter_raises_llm_auth_error_for_a_refused_key() -> None:
    chat_model = MagicMock(spec=BaseChatModel)
    chat_model.invoke.side_effect = RefusedKey("401 invalid api key")
    chat_model.with_structured_output.return_value = chat_model
    provider = LangChainProvider(
        name="test",
        chat_model=chat_model,
        provider_errors=(RefusedKey,),
        is_auth_error=lambda exc: isinstance(exc, RefusedKey),
    )

    with pytest.raises(LLMAuthError):
        provider.generate_text("q")
    with pytest.raises(LLMAuthError) as refused:
        provider.generate_structured("q", Answer)
    # Still a provider error: code that handles "provider down" (eval retries) keeps working.
    assert isinstance(refused.value, LLMProviderError)
    assert not isinstance(refused.value, LLMRateLimitError)
