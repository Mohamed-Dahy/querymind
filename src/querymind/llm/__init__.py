"""LLM provider interface and adapters (Groq, Gemini; Bedrock in Phase 6).

Import from here, not from the submodules:

    from querymind.llm import LLMProvider, create_llm_provider
"""

from querymind.llm.base import LLMProvider
from querymind.llm.embeddings import EmbeddingProvider
from querymind.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
)
from querymind.llm.factory import create_embedding_provider, create_llm_provider

__all__ = [
    "EmbeddingProvider",
    "LLMAuthError",
    "LLMError",
    "LLMOutputError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "create_embedding_provider",
    "create_llm_provider",
]
