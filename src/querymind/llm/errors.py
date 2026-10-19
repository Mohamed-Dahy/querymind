"""Errors raised by every LLM provider, so callers never depend on one vendor's exceptions."""


class LLMError(Exception):
    """Base class for all LLM failures."""


class LLMProviderError(LLMError):
    """The provider could not answer: network, timeout, rate limit, bad API key, unknown model."""


class LLMOutputError(LLMError):
    """The provider answered, but the output was empty or did not match the requested schema."""
