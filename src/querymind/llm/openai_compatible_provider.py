"""OpenAI-compatible adapter (5.8): OpenAI, OpenRouter, DeepSeek, Together... through one client.

Many providers serve the same HTTP API as OpenAI, so one LangChain `ChatOpenAI` reaches them all;
only the base URL differs. The base URL always comes from `llm/catalog.py`, never from a user.
"""

import openai
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from querymind.llm.catalog import CatalogEntry
from querymind.llm.langchain_provider import LangChainProvider, positive_seconds


def is_auth_error(exc: Exception) -> bool:
    """True if the provider refused the key: 401 (wrong key) or 403 (no access)."""
    return isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError)


def is_rate_limit(exc: Exception) -> bool:
    """True if the provider said "too many requests" (or the key is out of credit: also a 429)."""
    return isinstance(exc, openai.RateLimitError)


def retry_after_seconds(exc: Exception) -> float | None:
    """The provider's wait, from the `retry-after` header of its 429, if it sent one."""
    if not isinstance(exc, openai.APIStatusError):
        return None
    return positive_seconds(exc.response.headers.get("retry-after"))


class OpenAICompatibleProvider(LangChainProvider):
    """`LLMProvider` for any provider in the catalog with `adapter="openai_compatible"`."""

    def __init__(
        self,
        *,
        entry: CatalogEntry,
        model: str,
        api_key: SecretStr,
        temperature: float = 0.0,
        timeout_seconds: float = 60,
        max_retries: int = 2,
    ) -> None:
        if entry.adapter != "openai_compatible" or entry.base_url is None:
            raise ValueError(f"{entry.id} is not an OpenAI-compatible provider")
        chat_model = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=entry.base_url,
            # None = not sent: OpenAI's reasoning models refuse anything but their default.
            temperature=temperature if entry.sends_temperature else None,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )
        options = {"method": entry.structured_method} if entry.structured_method else {}
        super().__init__(
            name=entry.id,
            chat_model=chat_model,
            # openai.APIError covers connection errors, timeouts, rate limits, auth, server errors.
            provider_errors=(openai.APIError,),
            structured_output_options=options,
            is_rate_limit=is_rate_limit,
            retry_after_seconds=retry_after_seconds,
            is_auth_error=is_auth_error,
        )
