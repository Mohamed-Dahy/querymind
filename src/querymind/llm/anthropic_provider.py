"""Anthropic adapter (5.8): Claude models with the user's own Anthropic key."""

import anthropic
from langchain_anthropic import ChatAnthropic
from pydantic import SecretStr

from querymind.llm.langchain_provider import LangChainProvider, positive_seconds

# Anthropic requires a cap on the answer's length. Our longest answers (SQL with an explanation,
# or a summary) stay well under this.
MAX_OUTPUT_TOKENS = 4096


def is_auth_error(exc: Exception) -> bool:
    """True if Anthropic refused the key: 401 (wrong key) or 403 (no access)."""
    return isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError)


def is_rate_limit(exc: Exception) -> bool:
    """True if Anthropic said "too many requests"."""
    return isinstance(exc, anthropic.RateLimitError)


def retry_after_seconds(exc: Exception) -> float | None:
    """Anthropic's wait, from the `retry-after` header of its 429."""
    if not isinstance(exc, anthropic.APIStatusError):
        return None
    return positive_seconds(exc.response.headers.get("retry-after"))


class AnthropicProvider(LangChainProvider):
    """`LLMProvider` for Anthropic's Claude models (structured output through tool calling)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        temperature: float = 0.0,
        timeout_seconds: float = 60,
        max_retries: int = 2,
    ) -> None:
        chat_model = ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens_to_sample=MAX_OUTPUT_TOKENS,
            default_request_timeout=timeout_seconds,
            max_retries=max_retries,
            stop=None,
        )
        super().__init__(
            name="anthropic",
            chat_model=chat_model,
            provider_errors=(anthropic.APIError,),
            is_rate_limit=is_rate_limit,
            retry_after_seconds=retry_after_seconds,
            is_auth_error=is_auth_error,
        )
