"""Groq adapter: fast hosted open models, our default for development."""

import groq
from langchain_groq import ChatGroq
from pydantic import SecretStr

from querymind.llm.langchain_provider import LangChainProvider

# Models with Groq's *strict* structured outputs: the server constrains decoding so the answer
# always matches the JSON schema. With plain tool calling, gpt-oss sometimes answers in prose
# instead and Groq rejects it (400 "tool_use_failed"), about 1 call in 10 in our tests.
STRICT_JSON_SCHEMA_MODELS = frozenset({"openai/gpt-oss-20b", "openai/gpt-oss-120b"})

# Groq's 400 error codes for "the model answered, but its answer didn't fit the schema". Seen
# when the model refuses ("I can't help with that"), as plain text or with an empty `sql`:
# a bad answer, not an outage.
OUTPUT_ERROR_CODES = frozenset({"json_validate_failed", "tool_use_failed"})


def is_output_error(exc: Exception) -> bool:
    """True if Groq rejected the model's own answer (so retrying the same prompt won't help)."""
    if not isinstance(exc, groq.BadRequestError) or not isinstance(exc.body, dict):
        return False
    error = exc.body.get("error")  # the body is Groq's JSON: {"error": {"code": ..., ...}}
    return isinstance(error, dict) and error.get("code") in OUTPUT_ERROR_CODES


class GroqProvider(LangChainProvider):
    """`LLMProvider` for Groq's hosted models (e.g. openai/gpt-oss-120b)."""

    def __init__(
        self,
        *,
        model: str,
        api_key: SecretStr,
        temperature: float = 0.0,
        timeout_seconds: float = 60,
    ) -> None:
        chat_model = ChatGroq(
            model_name=model,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout_seconds,
            max_retries=2,  # retries rate limits and network blips, with backoff
        )
        # Other models keep LangChain's default (tool calling).
        options = (
            {"method": "json_schema", "strict": True} if model in STRICT_JSON_SCHEMA_MODELS else {}
        )
        super().__init__(
            name="groq",
            chat_model=chat_model,
            # groq.APIError covers connection errors, timeouts, rate limits, auth, server errors.
            provider_errors=(groq.APIError,),
            structured_output_options=options,
            is_output_error=is_output_error,
        )
