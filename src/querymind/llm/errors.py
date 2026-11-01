"""Errors raised by every LLM provider, so callers never depend on one vendor's exceptions."""


class LLMError(Exception):
    """Base class for all LLM failures."""


class LLMProviderError(LLMError):
    """The provider could not answer: network, timeout, bad API key, unknown model."""


class LLMRateLimitError(LLMProviderError):
    """The provider said "too many requests" (HTTP 429), even after the SDK's own retries.

    A subclass, so everything that handles "provider down" (eval retries, fallbacks) still works.
    `retry_after_seconds` is the provider's own wait, or None if it didn't give one.
    """

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class LLMOutputError(LLMError):
    """The provider answered, but the output was empty or did not match the requested schema."""
