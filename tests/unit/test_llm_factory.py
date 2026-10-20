"""Tests for create_llm_provider: config picks the right adapter with the right settings.

Building ChatGroq / ChatGoogleGenerativeAI makes no network call, so these stay offline.
"""

from typing import Any

import pytest
from pydantic import SecretStr

from querymind.config import Settings
from querymind.llm import create_llm_provider
from querymind.llm.gemini_provider import GeminiProvider
from querymind.llm.groq_provider import GroqProvider


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "llm_provider": "groq",
        "groq_api_key": SecretStr("gsk_test"),
        "groq_model": "openai/gpt-oss-120b",
        "google_api_key": SecretStr("google_test"),
        "gemini_model": "gemini-3.5-flash",
        "llm_temperature": 0.0,
        "llm_timeout_seconds": 30,
    }
    values.update(overrides)
    return Settings.model_validate(values)


def test_groq_settings_build_a_groq_provider() -> None:
    provider = create_llm_provider(make_settings())
    assert isinstance(provider, GroqProvider)
    assert provider.name == "groq"
    chat = provider._chat_model
    assert chat.model_name == "openai/gpt-oss-120b"  # type: ignore[attr-defined]
    # langchain-groq replaces exactly 0 with 1e-08 for Groq's API; it behaves the same.
    assert chat.temperature == pytest.approx(0.0, abs=1e-6)  # type: ignore[attr-defined]
    assert chat.request_timeout == 30  # type: ignore[attr-defined]
    assert chat.groq_api_key.get_secret_value() == "gsk_test"  # type: ignore[attr-defined]


def test_groq_gpt_oss_uses_strict_json_schema() -> None:
    provider = create_llm_provider(make_settings())
    assert provider._structured_output_options == {  # type: ignore[attr-defined]
        "method": "json_schema",
        "strict": True,
    }


def test_other_groq_models_keep_tool_calling() -> None:
    provider = create_llm_provider(make_settings(groq_model="llama-3.1-8b-instant"))
    assert provider._structured_output_options == {}  # type: ignore[attr-defined]


def test_gemini_settings_build_a_gemini_provider() -> None:
    provider = create_llm_provider(make_settings(llm_provider="gemini"))
    assert isinstance(provider, GeminiProvider)
    assert provider.name == "gemini"
    chat = provider._chat_model
    assert chat.model == "gemini-3.5-flash"  # type: ignore[attr-defined]
    assert chat.timeout == 30  # type: ignore[attr-defined]
    assert chat.google_api_key.get_secret_value() == "google_test"  # type: ignore[attr-defined]
