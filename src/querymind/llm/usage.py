"""What one run spent on the LLM (5.8b): its calls and their tokens, added up.

    meter = UsageMeter()
    llm = MeteredLLM(provider, meter)       # the run's LLM: every call is counted
    ...the run...
    meter.calls, meter.input_tokens, meter.output_tokens

One meter per run, handed to the run's LLM wrapper, so two questions answered at the same time
never add into each other (a shared counter, or one read from the logs, would mix them). A failed
call still counts: the provider adds what it knows before raising, because a reply that didn't
fit the schema was paid for too.
"""

import threading

from pydantic import BaseModel

from querymind.llm.base import LLMProvider


class UsageMeter:
    """Calls and tokens, added up. Safe to use from several threads (LangGraph may run nodes
    side by side)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._input = 0
        self._output = 0
        self._unknown = False  # a call whose provider didn't say its tokens

    def add(self, input_tokens: int | None, output_tokens: int | None) -> None:
        """One more call, with its tokens if the provider reported them."""
        with self._lock:
            self._calls += 1
            if input_tokens is None or output_tokens is None:
                self._unknown = True
            else:
                self._input += input_tokens
                self._output += output_tokens

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def input_tokens(self) -> int | None:
        """The total, or None if any call's tokens are unknown (a partial sum would mislead)."""
        return None if self._unknown else self._input

    @property
    def output_tokens(self) -> int | None:
        return None if self._unknown else self._output


class MeteredLLM(LLMProvider):
    """A provider whose every call is added to `meter`. Built per run around the run's LLM; the
    agent's nodes see an ordinary `LLMProvider`."""

    def __init__(self, inner: LLMProvider, meter: UsageMeter) -> None:
        self.name = inner.name
        self.model = inner.model
        self._inner = inner
        self._meter = meter

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        return self._inner.generate_text_metered(prompt, system=system, meter=self._meter)

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        return self._inner.generate_structured_metered(
            prompt, schema, system=system, meter=self._meter
        )
