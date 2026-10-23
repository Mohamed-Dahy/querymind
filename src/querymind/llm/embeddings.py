"""Embeddings: text → a list of numbers, so similar meanings end up close together (3.3).

    embeddings = create_embedding_provider(settings)      # None when GOOGLE_API_KEY isn't set
    vectors = embeddings.embed_documents(["Table customer: music store customers ..."])
    query = embeddings.embed_query("Which music customers live in Brazil?")

Schema retrieval embeds one short document per table once (scripts/build_index.py) and the
question each time; the tables whose vectors point the same way as the question's are sent to
the LLM. Same idea as `LLMProvider`: the rest of the code only sees `EmbeddingProvider`, so the
vendor can change (Titan on Bedrock in Phase 6).

Every vector is scaled to length 1. Gemini returns unit vectors only at its full 3072
dimensions; at 768 they are about 0.6 long (measured in 3.3's probe). Cosine distance doesn't
care, but unit vectors make the stored numbers comparable and inner product usable later.
"""

import hashlib
import math
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence

from langchain_google_genai import GoogleGenerativeAIEmbeddings

# The library wraps every failure (bad key, quota, network) in this one error. It isn't
# exported at the top level, so it's imported from where it's defined.
from langchain_google_genai._common import GoogleGenerativeAIError
from pydantic import SecretStr

from querymind.llm.errors import LLMProviderError

DIMENSIONS = 768  # the index column is vector(768); every provider must return this size


class EmbeddingProvider(ABC):
    """Turn texts into unit vectors of `DIMENSIONS` numbers."""

    #: Short id for logs and the index, e.g. "gemini-embedding-001".
    name: str

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for texts that will be searched (the table documents).

        Raises:
            LLMProviderError: the provider could not be reached or refused the request.
        """

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """The vector for a search text (the question).

        Raises:
            LLMProviderError: the provider could not be reached or refused the request.
        """


def unit(vector: Sequence[float]) -> list[float]:
    """The same direction, length 1. A zero vector stays zero (it matches nothing)."""
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else list(vector)


class GeminiEmbeddings(EmbeddingProvider):
    """Google's `gemini-embedding-001`, cut to 768 dimensions.

    Task types tell the model which side of the search a text is on: RETRIEVAL_DOCUMENT for
    what is stored, RETRIEVAL_QUERY for the question. The two are trained to match each other.
    """

    def __init__(self, *, model: str, api_key: SecretStr) -> None:
        self.name = model
        self._client = GoogleGenerativeAIEmbeddings(
            model=model, google_api_key=api_key, output_dimensionality=DIMENSIONS
        )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        try:
            vectors = self._client.embed_documents(list(texts), task_type="RETRIEVAL_DOCUMENT")
        except GoogleGenerativeAIError as exc:
            raise LLMProviderError(f"{self.name} embedding request failed: {exc}") from exc
        return [unit(v) for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        try:
            vector = self._client.embed_query(text, task_type="RETRIEVAL_QUERY")
        except GoogleGenerativeAIError as exc:
            raise LLMProviderError(f"{self.name} embedding request failed: {exc}") from exc
        return unit(vector)


class FakeEmbeddings(EmbeddingProvider):
    """Deterministic vectors for tests and CI (no network, no key).

    Not random: each word is hashed to one of the 768 positions, so texts that share words
    point the same way. "music customers" lands nearer "Table customer: music store customers"
    than "Table shippers", which lets tests check real ranking logic without an API.
    """

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0  # embedding calls made, so tests can check what was (not) re-embedded

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        self.calls += 1
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * DIMENSIONS
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(word.encode()).digest()
            vector[int.from_bytes(digest[:4], "big") % DIMENSIONS] += 1.0
        return unit(vector)
