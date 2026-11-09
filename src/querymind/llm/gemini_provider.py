"""Gemini adapter: Google's hosted models through the Gemini API (Google AI Studio key)."""

import httpx
from google.genai import errors as genai_errors
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from pydantic import SecretStr

from querymind.llm.langchain_provider import LangChainProvider, positive_seconds


def _client_error(exc: Exception) -> genai_errors.ClientError | None:
    """The SDK's error behind `exc`: LangChain wraps it and keeps it as the `__cause__`."""
    for candidate in (exc, exc.__cause__):
        if isinstance(candidate, genai_errors.ClientError):
            return candidate
    return None


def is_rate_limit(exc: Exception) -> bool:
    """True if Gemini said "too many requests" (RESOURCE_EXHAUSTED)."""
    error = _client_error(exc)
    return error is not None and error.code == 429


def is_auth_error(exc: Exception) -> bool:
    """True if Google refused the key. A wrong key is *not* a 401 here: it is a 400 whose body
    says `"reason": "API_KEY_INVALID"` (seen in 5.8). 401 / 403: no access to this API or model."""
    error = _client_error(exc)
    if error is None:
        return False
    if error.code in (401, 403):
        return True
    body = error.details
    inner = body.get("error") if isinstance(body, dict) else None
    details = inner.get("details") if isinstance(inner, dict) else None
    return any(
        isinstance(detail, dict) and detail.get("reason") == "API_KEY_INVALID"
        for detail in (details if isinstance(details, list) else [])
    )


def retry_after_seconds(exc: Exception) -> float | None:
    """Gemini's wait, from the body of its 429: error.details[].retryDelay, e.g. "17s".

    The body is Google's JSON, so nothing about its shape is taken for granted.
    """
    error = _client_error(exc)
    body = error.details if error is not None else None
    inner = body.get("error") if isinstance(body, dict) else None
    details = inner.get("details") if isinstance(inner, dict) else None
    for detail in details if isinstance(details, list) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str):
            return positive_seconds(delay.removesuffix("s"))
    return None


class GeminiProvider(LangChainProvider):
    """`LLMProvider` for Google's Gemini models (e.g. gemini-3.5-flash).

    No `temperature`: Gemini 3.x models use fixed sampling and ignore it (the library
    warns on every call if it's sent), so LLM_TEMPERATURE only applies to Groq.
    """

    def __init__(
        self, *, model: str, api_key: SecretStr, timeout_seconds: float = 60, max_retries: int = 2
    ) -> None:
        chat_model = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,  # the library's default is 6: keep failures fast, as Groq
        )
        super().__init__(
            name="gemini",
            chat_model=chat_model,
            provider_errors=(
                ChatGoogleGenerativeAIError,  # LangChain's wrapper around API failures
                genai_errors.APIError,  # raw SDK errors: bad key, quota, unknown model, 5xx
                # No network (DNS, connection refused, timeout): the SDK lets httpx's own errors
                # through. Found in 4.4, when the Wi-Fi dropped and crashed a whole eval run.
                httpx.TransportError,
            ),
            is_rate_limit=is_rate_limit,
            retry_after_seconds=retry_after_seconds,
            is_auth_error=is_auth_error,
        )
