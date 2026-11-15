"""A fake `LLMProvider` for unit tests: returns scripted answers and records every call.

Unit tests must never call a real LLM (slow, costs money, different answer each time),
so they use this instead:

    fake = FakeLLMProvider([SqlDraft(sql="SELECT 1", ...)])
    run_something(llm=fake)
    assert "orders" in fake.calls[0].prompt
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel

from querymind.llm.base import LLMProvider
from querymind.llm.errors import LLMOutputError
from querymind.llm.usage import UsageMeter

type FakeResponse = str | BaseModel | Exception


@dataclass(frozen=True)
class FakeCall:
    """One recorded call to the fake provider."""

    prompt: str
    system: str | None
    schema: type[BaseModel] | None  # None for generate_text


class FakeLLMProvider(LLMProvider):
    """Answers with the queued responses, in order. A queued Exception is raised instead."""

    name = "fake"

    def __init__(
        self,
        responses: Iterable[FakeResponse] = (),
        *,
        model: str = "fake-model",
        usage: tuple[int, int] | None = None,
    ) -> None:
        """`usage`: the (input, output) tokens each call reports to a meter (5.8b); None = the
        provider doesn't say, like a provider without token counts."""
        self._responses: deque[FakeResponse] = deque(responses)
        self.calls: list[FakeCall] = []
        self.model = model
        self._usage = usage

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        self.calls.append(FakeCall(prompt, system, None))
        response = self._next()
        if not isinstance(response, str):
            raise TypeError(
                f"FakeLLMProvider: queued {type(response).__name__}, test asked for text"
            )
        return response

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        self.calls.append(FakeCall(prompt, system, schema))
        response = self._next()
        if not isinstance(response, schema):
            # Same error a real provider raises for output that doesn't fit the schema.
            raise LLMOutputError(
                f"fake returned {type(response).__name__}, expected {schema.__name__}"
            )
        return response

    def generate_text_metered(
        self, prompt: str, *, system: str | None = None, meter: UsageMeter
    ) -> str:
        try:
            return self.generate_text(prompt, system=system)
        finally:
            meter.add(*(self._usage or (None, None)))

    def generate_structured_metered[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None, meter: UsageMeter
    ) -> T:
        try:
            return self.generate_structured(prompt, schema, system=system)
        finally:
            meter.add(*(self._usage or (None, None)))

    def _next(self) -> str | BaseModel:
        if not self._responses:
            raise RuntimeError("FakeLLMProvider has no responses left: queue more in the test")
        response = self._responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response
