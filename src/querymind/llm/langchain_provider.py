"""Shared adapter: turns any LangChain chat model into an `LLMProvider`.

Groq and Gemini only differ in how their chat model is built, so the calling,
error handling and logging all live here, once.

Every call logs one "llm call" line with its duration and the tokens it used (input = the
prompt, output = the answer), as the provider reports them. Tokens are what LLM APIs charge
for, so this line is the base of cost tracking (the eval adds them up per question, 4.2).
"""

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from langchain_core.exceptions import OutputParserException
from langchain_core.language_models import BaseChatModel, LanguageModelInput
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
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
        structured_output_options: Mapping[str, Any] | None = None,
        is_output_error: Callable[[Exception], bool] | None = None,
    ) -> None:
        """
        Args:
            name: provider id for logs and errors, e.g. "groq".
            chat_model: the configured LangChain chat model.
            provider_errors: the vendor exceptions that mean "the provider could not answer";
                they are re-raised as `LLMProviderError`.
            structured_output_options: extra arguments for LangChain's `with_structured_output`,
                e.g. `{"method": "json_schema", "strict": True}`. Empty = LangChain's default.
            is_output_error: picks out the provider errors that mean "the model answered, but
                not in the requested format" (e.g. it refused in plain text); they are re-raised
                as `LLMOutputError` instead, because retrying won't help.
        """
        self.name = name
        self._chat_model = chat_model
        self._provider_errors = provider_errors
        self._structured_output_options = dict(structured_output_options or {})
        self._is_output_error = is_output_error or (lambda _exc: False)

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
        # and parses the reply into a Pydantic object for us. include_raw=True also hands back
        # the raw message (it carries the token counts), as {"raw", "parsed", "parsing_error"}:
        # a reply that doesn't fit the schema then arrives as `parsing_error`, not as an exception.
        structured_model = self._chat_model.with_structured_output(
            schema, include_raw=True, **self._structured_output_options
        )
        reply = self._invoke(structured_model, prompt, system, kind=f"structured:{schema.__name__}")
        error = reply.get("parsing_error") if isinstance(reply, dict) else None
        if error is not None:
            raise LLMOutputError(f"{self.name} output did not match the schema: {error}") from error
        result = reply.get("parsed") if isinstance(reply, dict) else None
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
        reply: Any = None
        try:
            reply = runnable.invoke(messages)
            return reply
        except self._provider_errors as exc:
            if self._is_output_error(exc):
                raise LLMOutputError(f"{self.name} output did not match the schema: {exc}") from exc
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
                    **token_usage(reply),
                },
            )


def token_usage(reply: object) -> dict[str, int | None]:
    """Input and output tokens of one reply (None when the provider didn't say, or it failed).

    LangChain puts them on the AI message as `usage_metadata`; a structured reply carries that
    message under "raw".
    """
    message = reply.get("raw") if isinstance(reply, dict) else reply
    usage = message.usage_metadata if isinstance(message, AIMessage) else None
    if not usage:
        return {"input_tokens": None, "output_tokens": None}
    return {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}
