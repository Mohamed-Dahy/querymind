"""Tests for FakeLLMProvider, the stand-in every future unit test will use instead of a real LLM."""

import pytest
from pydantic import BaseModel

from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.fake import FakeCall, FakeLLMProvider


class Answer(BaseModel):
    value: int


def test_returns_queued_responses_in_order() -> None:
    fake = FakeLLMProvider(["first", Answer(value=2)])
    assert fake.generate_text("a") == "first"
    assert fake.generate_structured("b", Answer) == Answer(value=2)


def test_records_every_call() -> None:
    fake = FakeLLMProvider(["ok", Answer(value=1)])
    fake.generate_text("hello", system="be brief")
    fake.generate_structured("give a number", Answer)
    assert fake.calls == [
        FakeCall("hello", "be brief", None),
        FakeCall("give a number", None, Answer),
    ]


def test_queued_exception_is_raised() -> None:
    fake = FakeLLMProvider([LLMProviderError("rate limited")])
    with pytest.raises(LLMProviderError, match="rate limited"):
        fake.generate_text("a")


def test_wrong_schema_type_is_an_output_error() -> None:
    fake = FakeLLMProvider(["just text"])
    with pytest.raises(LLMOutputError):
        fake.generate_structured("a", Answer)


def test_running_out_of_responses_is_a_clear_error() -> None:
    fake = FakeLLMProvider()
    with pytest.raises(RuntimeError, match="no responses left"):
        fake.generate_text("a")
