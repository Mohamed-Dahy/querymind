"""Embeddings (3.3): unit vectors, the fake's word-based similarity, Gemini errors mapped."""

import math
from unittest.mock import MagicMock

import pytest
from langchain_google_genai._common import GoogleGenerativeAIError
from pydantic import SecretStr

from querymind.llm import LLMProviderError
from querymind.llm.embeddings import DIMENSIONS, FakeEmbeddings, GeminiEmbeddings, unit


def length(vector: list[float]) -> float:
    return math.sqrt(sum(x * x for x in vector))


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))  # both are unit vectors


def test_unit_scales_to_length_one() -> None:
    assert unit([3.0, 4.0]) == [0.6, 0.8]
    assert unit([0.0, 0.0]) == [0.0, 0.0]  # nothing to scale: matches nothing


def test_fake_vectors_are_stable_unit_vectors() -> None:
    fake = FakeEmbeddings()
    vector = fake.embed_query("music store customers")
    assert len(vector) == DIMENSIONS
    assert length(vector) == pytest.approx(1.0)
    assert vector == FakeEmbeddings().embed_query("Music store CUSTOMERS!")  # words, not case
    assert fake.calls == 1


def test_fake_texts_that_share_words_are_closer() -> None:
    fake = FakeEmbeddings()
    question = fake.embed_query("Which music store customers live in Brazil?")
    customer, shippers = fake.embed_documents(
        ["Table customer: music store customers, country", "Table shippers: delivery companies"]
    )
    assert cosine(question, customer) > cosine(question, shippers)


def gemini_with(client: MagicMock) -> GeminiEmbeddings:
    embeddings = GeminiEmbeddings(model="gemini-embedding-001", api_key=SecretStr("test-key"))
    embeddings._client = client  # no network: the LangChain client is replaced
    return embeddings


def test_gemini_sends_task_types_and_returns_unit_vectors() -> None:
    client = MagicMock()
    client.embed_documents.return_value = [[0.3, 0.4]]
    client.embed_query.return_value = [0.0, 0.5]
    embeddings = gemini_with(client)

    assert embeddings.embed_documents(["Table customers"])[0] == pytest.approx([0.6, 0.8])
    assert embeddings.embed_query("clients?") == [0.0, 1.0]
    client.embed_documents.assert_called_once_with(
        ["Table customers"], task_type="RETRIEVAL_DOCUMENT"
    )
    client.embed_query.assert_called_once_with("clients?", task_type="RETRIEVAL_QUERY")


def test_gemini_errors_become_provider_errors() -> None:
    client = MagicMock()
    client.embed_query.side_effect = GoogleGenerativeAIError("Error embedding content (429): quota")
    with pytest.raises(LLMProviderError, match="gemini-embedding-001 embedding request failed"):
        gemini_with(client).embed_query("anything")
