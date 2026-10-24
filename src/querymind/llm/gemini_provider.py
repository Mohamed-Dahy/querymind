"""Gemini adapter: Google's hosted models through the Gemini API (Google AI Studio key)."""

import httpx
from google.genai import errors as genai_errors
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from pydantic import SecretStr

from querymind.llm.langchain_provider import LangChainProvider


class GeminiProvider(LangChainProvider):
    """`LLMProvider` for Google's Gemini models (e.g. gemini-3.5-flash).

    No `temperature`: Gemini 3.x models use fixed sampling and ignore it (the library
    warns on every call if it's sent), so LLM_TEMPERATURE only applies to Groq.
    """

    def __init__(self, *, model: str, api_key: SecretStr, timeout_seconds: float = 60) -> None:
        chat_model = ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            timeout=timeout_seconds,
            max_retries=2,  # the library default is 6; keep failures fast and the same as Groq
        )
        super().__init__(
            name="gemini",
            chat_model=chat_model,
            provider_errors=(
                ChatGoogleGenerativeAIError,  # LangChain's wrapper around API failures
                genai_errors.APIError,  # raw SDK errors: bad key, quota, unknown model, 5xx
                # No network (DNS, connection refused, timeout): the SDK lets httpx's own errors
                # through. Found in 4.4, when the Wi-Fi dropped and crashed a whole eval run.
                httpx.TransportError,
            ),
        )
