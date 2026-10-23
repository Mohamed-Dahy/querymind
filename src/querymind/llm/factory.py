"""Build the configured LLM provider. The only place that knows which vendors exist."""

from typing import assert_never

from querymind.config import Settings
from querymind.llm.base import LLMProvider
from querymind.llm.embeddings import EmbeddingProvider, GeminiEmbeddings
from querymind.llm.gemini_provider import GeminiProvider
from querymind.llm.groq_provider import GroqProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    """Return the provider chosen by `settings.llm_provider` (LLM_PROVIDER in .env)."""
    model, api_key = settings.llm_credentials()
    temperature = settings.llm_temperature
    timeout = settings.llm_timeout_seconds
    match settings.llm_provider:
        case "groq":
            return GroqProvider(
                model=model, api_key=api_key, temperature=temperature, timeout_seconds=timeout
            )
        case "gemini":
            return GeminiProvider(model=model, api_key=api_key, timeout_seconds=timeout)
        case _:
            # mypy proves every LLM_PROVIDER value is handled above: adding a provider
            # to the Literal in config.py without a `case` here fails type-checking.
            assert_never(settings.llm_provider)


def create_embedding_provider(settings: Settings) -> EmbeddingProvider | None:
    """Gemini embeddings for schema retrieval (3.3), or None when GOOGLE_API_KEY isn't set.

    Separate from the LLM: the agent can write SQL with Groq and still search with Google.
    """
    if settings.google_api_key is None:
        return None
    return GeminiEmbeddings(model=settings.embedding_model, api_key=settings.google_api_key)
