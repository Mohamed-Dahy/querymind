"""Groq adapter: fast hosted open models, our default for development."""

import groq
from langchain_groq import ChatGroq
from pydantic import SecretStr

from querymind.llm.langchain_provider import LangChainProvider


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
        # groq.APIError covers connection errors, timeouts, rate limits, auth and server errors.
        super().__init__(name="groq", chat_model=chat_model, provider_errors=(groq.APIError,))
