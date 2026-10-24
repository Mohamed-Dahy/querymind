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
from querymind.llm.langchain_provider import LangChainProvider, token_usage


class Answer(BaseModel):
    value: int


class Other(BaseModel):
    name: str


USAGE = {"input_tokens": 1200, "output_tokens": 80, "total_tokens": 1280}


def structured(parsed: object, error: Exception | None = None) -> dict[str, object]:
    """What LangChain's with_structured_output(..., include_raw=True) returns."""
    raw = AIMessage("{...}", usage_metadata=USAGE)
    return {"raw": raw, "parsed": parsed, "parsing_error": error}


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
    chat_model.with_structured_output.return_value.invoke.return_value = structured(
        Answer(value=42)
    )
    result = provider.generate_structured("question", Answer)
    assert result == Answer(value=42)
    chat_model.with_structured_output.assert_called_once_with(Answer, include_raw=True)


def test_structured_output_options_are_passed_to_langchain(chat_model: MagicMock) -> None:
    provider = LangChainProvider(
        name="test",
        chat_model=chat_model,
        provider_errors=(VendorDown,),
        structured_output_options={"method": "json_schema", "strict": True},
    )
    chat_model.with_structured_output.return_value.invoke.return_value = structured(Answer(value=1))
    provider.generate_structured("question", Answer)
    chat_model.with_structured_output.assert_called_once_with(
        Answer, include_raw=True, method="json_schema", strict=True
    )


def test_wrong_structured_type_is_an_output_error(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    chat_model.with_structured_output.return_value.invoke.return_value = structured(Other(name="x"))
    with pytest.raises(LLMOutputError, match="expected Answer"):
        provider.generate_structured("question", Answer)


def test_unparseable_output_is_an_output_error(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    error = OutputParserException("not valid JSON")
    chat_model.with_structured_output.return_value.invoke.return_value = structured(None, error)
    with pytest.raises(LLMOutputError, match="did not match the schema"):
        provider.generate_structured("question", Answer)
    # A parser that raises instead (LangChain does for some formats): the same error for us.
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


def test_vendor_errors_about_the_answer_become_output_errors(chat_model: MagicMock) -> None:
    # e.g. Groq's 400 "json_validate_failed": the model refused in plain text. Not an outage.
    provider = LangChainProvider(
        name="test",
        chat_model=chat_model,
        provider_errors=(VendorDown,),
        is_output_error=lambda exc: "refused" in str(exc),
    )
    chat_model.invoke.side_effect = VendorDown("model refused")
    with pytest.raises(LLMOutputError, match="did not match the schema"):
        provider.generate_text("question")
    chat_model.invoke.side_effect = VendorDown("rate limited")
    with pytest.raises(LLMProviderError):
        provider.generate_text("question")


def test_unexpected_errors_are_not_hidden(
    provider: LangChainProvider, chat_model: MagicMock
) -> None:
    # A bug in our code (not a vendor failure) must surface as itself.
    chat_model.invoke.side_effect = KeyError("bug")
    with pytest.raises(KeyError):
        provider.generate_text("question")


def test_each_call_logs_its_tokens(
    provider: LangChainProvider, chat_model: MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    chat_model.with_structured_output.return_value.invoke.return_value = structured(Answer(value=1))
    chat_model.invoke.return_value = AIMessage("hi", usage_metadata=USAGE)
    with caplog.at_level("INFO", logger="querymind.llm"):
        provider.generate_structured("question", Answer)
        provider.generate_text("question")
    calls = [r for r in caplog.records if r.getMessage() == "llm call"]
    tokens = [(getattr(r, "input_tokens", 0), getattr(r, "output_tokens", 0)) for r in calls]
    assert tokens == [(1200, 80), (1200, 80)]


def test_token_usage_unknown_or_failed() -> None:
    assert token_usage(AIMessage("no usage")) == {"input_tokens": None, "output_tokens": None}
    assert token_usage(None) == {"input_tokens": None, "output_tokens": None}  # the call failed
