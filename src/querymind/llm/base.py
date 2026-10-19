"""The one interface every LLM provider implements (Groq, Gemini, later Bedrock, and the test fake).

The rest of QueryMind only talks to `LLMProvider`, never to a vendor SDK, so the provider
can be swapped with one config change (LLM_PROVIDER in .env).
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel


class LLMProvider(ABC):
    """Generate text or validated structured output from a prompt."""

    #: Short provider id used in logs and errors, e.g. "groq".
    name: str

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
