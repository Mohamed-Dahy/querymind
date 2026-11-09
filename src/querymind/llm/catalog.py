"""The fixed list of LLM providers a user can bring a key for (5.8, ADR 010).

Each entry says which adapter talks to it and, for OpenAI-compatible APIs, the one base URL we
call. There is **no free-form base URL** on purpose: a URL typed by a user would let them make our
server call any address (SSRF: internal services, the cloud's metadata endpoint), and would bring
local models back in. A new provider is a code change here, reviewed like any other.

The key prefixes are what the log redaction filter (logging_setup.py) recognises, so a key quoted
in an error can't reach the logs. A provider whose keys have no known prefix says so.
"""

from dataclasses import dataclass
from typing import Literal

ProviderId = Literal["groq", "gemini", "openai", "openrouter", "deepseek", "together", "anthropic"]
Adapter = Literal["groq", "gemini", "openai_compatible", "anthropic"]


@dataclass(frozen=True)
class CatalogEntry:
    """One provider users can choose."""

    id: ProviderId
    label: str  # what the UI shows
    adapter: Adapter
    example_model: str  # a model id the provider serves, shown as a hint (the test call decides)
    keys_url: str  # where a user makes a key
    base_url: str | None = None  # OpenAI-compatible only: the one address we call
    # How structured output is asked for: None = the adapter's default. Tool calling works on
    # nearly every OpenAI-compatible server; OpenAI itself does strict JSON schema natively.
    structured_method: Literal["json_schema", "function_calling"] | None = None
    # OpenAI's reasoning models refuse temperature=0 (only the default is allowed), so it is not
    # sent there; the others accept 0, the most predictable output for SQL.
    sends_temperature: bool = True
    key_prefix: str | None = None  # None: no known prefix, so log redaction can't recognise it


CATALOG: dict[ProviderId, CatalogEntry] = {
    entry.id: entry
    for entry in (
        CatalogEntry(
            id="groq",
            label="Groq",
            adapter="groq",
            example_model="openai/gpt-oss-120b",
            keys_url="https://console.groq.com/keys",
            key_prefix="gsk_",
        ),
        CatalogEntry(
            id="gemini",
            label="Google Gemini",
            adapter="gemini",
            example_model="gemini-3.5-flash",
            keys_url="https://aistudio.google.com/apikey",
            key_prefix="AIza",
        ),
        CatalogEntry(
            id="openai",
            label="OpenAI",
            adapter="openai_compatible",
            example_model="gpt-4.1-mini",
            keys_url="https://platform.openai.com/api-keys",
            base_url="https://api.openai.com/v1",
            structured_method="json_schema",
            sends_temperature=False,
            key_prefix="sk-",
        ),
        CatalogEntry(
            id="openrouter",
            label="OpenRouter",
            adapter="openai_compatible",
            example_model="openai/gpt-4.1-mini",
            keys_url="https://openrouter.ai/keys",
            base_url="https://openrouter.ai/api/v1",
            structured_method="function_calling",
            key_prefix="sk-",  # sk-or-...
        ),
        CatalogEntry(
            id="deepseek",
            label="DeepSeek",
            adapter="openai_compatible",
            example_model="deepseek-chat",
            keys_url="https://platform.deepseek.com/api_keys",
            base_url="https://api.deepseek.com/v1",
            structured_method="function_calling",
            key_prefix="sk-",
        ),
        CatalogEntry(
            id="together",
            label="Together AI",
            adapter="openai_compatible",
            example_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
            keys_url="https://api.together.ai/settings/api-keys",
            base_url="https://api.together.xyz/v1",
            structured_method="function_calling",
            key_prefix=None,  # older keys are bare hex: no shape to recognise in a log line
        ),
        CatalogEntry(
            id="anthropic",
            label="Anthropic (Claude)",
            adapter="anthropic",
            example_model="claude-haiku-4-5",
            keys_url="https://console.anthropic.com/settings/keys",
            key_prefix="sk-",  # sk-ant-...
        ),
    )
}


def entry_for(provider: ProviderId) -> CatalogEntry:
    """The catalog entry of a provider id (already validated by its Literal type)."""
    return CATALOG[provider]
