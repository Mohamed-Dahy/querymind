"""The one interface every LLM provider implements (Groq, Gemini, later Bedrock, and the test fake).

The rest of QueryMind only talks to `LLMProvider`, never to a vendor SDK, so the provider
can be swapped with one config change (LLM_PROVIDER in .env).
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # usage.py imports this module: only the type is needed here
    from querymind.llm.usage import UsageMeter


class LLMProvider(ABC):
    """Generate text or validated structured output from a prompt."""

    #: Short provider id used in logs and errors, e.g. "groq".
    name: str
    #: The provider's model id, e.g. "openai/gpt-oss-20b" (5.8b: shown with each answer, priced).
    model: str = ""

    @abstractmethod
    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        """Return the model's plain-text answer to `prompt`.

        Raises:
            LLMProviderError: the provider could not be reached or refused the request.
            LLMOutputError: the answer was empty.
        """

    @abstractmethod
    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        """Return the model's answer as an instance of `schema`, already validated by Pydantic.

        Raises:
            LLMProviderError: the provider could not be reached or refused the request.
            LLMOutputError: the answer did not match `schema`.
        """

    def generate_text_metered(
        self, prompt: str, *, system: str | None = None, meter: "UsageMeter"
    ) -> str:
        """`generate_text`, adding the call to `meter` (5.8b), also when it fails. This default
        can't see tokens, so it counts the call with unknown tokens; providers that know them
        (llm/langchain_provider.py) override it."""
        try:
            return self.generate_text(prompt, system=system)
        finally:
            meter.add(None, None)

    def generate_structured_metered[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None, meter: "UsageMeter"
    ) -> T:
        """`generate_structured`, adding the call to `meter` (as `generate_text_metered`)."""
        try:
            return self.generate_structured(prompt, schema, system=system)
        finally:
            meter.add(None, None)
