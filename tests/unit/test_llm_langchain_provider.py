"""Tests for LangChainProvider: messages sent, results returned, vendor errors translated.

The LangChain chat model is replaced by a MagicMock, so no real LLM is ever called.
"""

from unittest.mock import MagicMock

import pytest
from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.langchain_provider import LangChainProvider


class Answer(BaseModel):
    value: int


class Other(BaseModel):
    name: str


class VendorDown(Exception):
    """Stands in for a vendor SDK error like groq.APIConnectionError."""


@pytest.fixture
def chat_model() -> MagicMock:
    return MagicMock(spec=BaseChatModel)


@pytest.fixture
def provider(chat_model: MagicMock) -> LangChainProvider:
    return LangChainProvider(name="test", chat_model=chat_model, provider_errors=(VendorDown,))


def test_generate_text_returns_the_answer(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.invoke.return_value = AIMessage("Paris")
    assert provider.generate_text("Capital of France?") == "Paris"


def test_system_prompt_is_sent_first(provider: LangChainProvider, chat_model: MagicMock) -> None:
    chat_model.invoke.return_value = AIMessage("ok")
    provider.generate_text("question", system="be brief")
    messages = chat_model.invoke.call_args.args[0]
    assert messages == [SystemMessage("be brief"), HumanMessage("question")]


def test_no_system_prompt_sends_only_the_question(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.invoke.return_value = AIMessage("ok")
    provider.generate_text("question")
    assert chat_model.invoke.call_args.args[0] == [HumanMessage("question")]


def test_empty_text_is_an_output_error(provider: LangChainProvider, chat_model: MagicMock) -> None:
    chat_model.invoke.return_value = AIMessage("   ")
    with pytest.raises(LLMOutputError, match="empty"):
        provider.generate_text("question")


def test_generate_structured_returns_the_schema_object(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.with_structured_output.return_value.invoke.return_value = Answer(value=42)
    result = provider.generate_structured("question", Answer)
    assert result == Answer(value=42)
    chat_model.with_structured_output.assert_called_once_with(Answer)


def test_wrong_structured_type_is_an_output_error(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.with_structured_output.return_value.invoke.return_value = Other(name="x")
    with pytest.raises(LLMOutputError, match="expected Answer"):
        provider.generate_structured("question", Answer)


def test_unparseable_output_is_an_output_error(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    error = OutputParserException("not valid JSON")
    chat_model.with_structured_output.return_value.invoke.side_effect = error
    with pytest.raises(LLMOutputError, match="did not match the schema"):
        provider.generate_structured("question", Answer)


def test_vendor_errors_become_provider_errors(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.invoke.side_effect = VendorDown("connection refused")
    with pytest.raises(LLMProviderError, match="test request failed: connection refused") as info:
        provider.generate_text("question")
    assert isinstance(info.value.__cause__, VendorDown)  # original error kept for debugging


def test_unexpected_errors_are_not_hidden(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    # A bug in our code (not a vendor failure) must surface as itself.
    chat_model.invoke.side_effect = KeyError("bug")
    with pytest.raises(KeyError):
        provider.generate_text("question")
