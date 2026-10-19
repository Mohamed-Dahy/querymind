"""Shared adapter: turns any LangChain chat model into an `LLMProvider`.

Groq and Gemini only differ in how their chat model is built, so the calling,
error handling and logging all live here, once.
"""

import logging
import time
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import Runnable
from pydantic import BaseModel, ValidationError

from querymind.llm.base import LLMProvider
from querymind.llm.errors import LLMOutputError, LLMProviderError

logger = logging.getLogger(__name__)


class LangChainProvider(LLMProvider):
    """An `LLMProvider` backed by a LangChain chat model."""

    def __init__(
        self,
        *,
        name: str,
        chat_model: BaseChatModel,
        provider_errors: tuple[type[Exception], ...],
    ) -> None:
        """
        Args:
            name: provider id for logs and errors, e.g. "groq".
            chat_model: the configured LangChain chat model.
            provider_errors: the vendor exceptions that mean "the provider could not answer";
                they are re-raised as `LLMProviderError`.
        """
        self.name = name
        self._chat_model = chat_model
        self._provider_errors = provider_errors

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        message = self._invoke(self._chat_model, prompt, system, kind="text")
        text = message.text if isinstance(message, BaseMessage) else ""
        if not text.strip():
            raise LLMOutputError(f"{self.name} returned an empty answer")
        return text

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        # LangChain asks the model to fill `schema` (via tool calling or JSON schema)
        # and parses the reply into a Pydantic object for us.
        structured_model = self._chat_model.with_structured_output(schema)
        result = self._invoke(
            structured_model, prompt, system, kind=f"structured:{schema.__name__}"
        )
        if not isinstance(result, schema):
            raise LLMOutputError(
                f"{self.name} returned {type(result).__name__}, expected {schema.__name__}"
            )
        return result

    def _invoke(
        self,
        runnable: Runnable[LanguageModelInput, Any],
        prompt: str,
        system: str | None,
        *,
        kind: str,
    ) -> Any:
        """Call the model, translate vendor errors into ours, and log how long it took."""
        messages: list[BaseMessage] = [HumanMessage(prompt)]
        if system:
            messages.insert(0, SystemMessage(system))

        started = time.perf_counter()
        try:
            return runnable.invoke(messages)
        except self._provider_errors as exc:
            raise LLMProviderError(f"{self.name} request failed: {exc}") from exc
        except (OutputParserException, ValidationError) as exc:
            raise LLMOutputError(f"{self.name} output did not match the schema: {exc}") from exc
        finally:
            logger.info(
                "llm call",
                extra={
                    "provider": self.name,
                    "kind": kind,
                    "duration_ms": round((time.perf_counter() - started) * 1000),
                },
            )
