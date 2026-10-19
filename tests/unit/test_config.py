"""Tests for querymind.config: values load, types are checked, and bad config fails clearly."""

from pathlib import Path

import pytest

from querymind.config import ConfigError, Settings, load_settings

VALID_ENV = {
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5433",
    "POSTGRES_DB": "northwind",
    "POSTGRES_ADMIN_USER": "admin",
    "POSTGRES_ADMIN_PASSWORD": "admin-secret",
    "ANALYST_RO_USER": "analyst_ro",
    "ANALYST_RO_PASSWORD": "ro-secret",
    "LLM_PROVIDER": "groq",
    "GROQ_API_KEY": "gsk-secret",
    "GROQ_MODEL": "groq-model",
}


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Remove every QueryMind variable from the environment, so tests never see the real .env."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


@pytest.fixture
def valid_env(clean_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A complete, valid environment."""
    for name, value in VALID_ENV.items():
        clean_env.setenv(name, value)
    return clean_env


def test_loads_values_and_converts_types(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    assert settings.postgres_port == 5433  # "5433" text became an int
    assert settings.llm_provider == "groq"
    assert settings.analyst_ro_password.get_secret_value() == "ro-secret"


def test_limits_have_safe_defaults(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    assert settings.query_timeout_seconds == 10
    assert settings.max_rows == 1000


def test_missing_required_variables_fail_with_their_names(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("POSTGRES_HOST", "127.0.0.1")
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=None)
    message = str(exc_info.value)
    assert "POSTGRES_ADMIN_PASSWORD: missing" in message
    assert "LLM_PROVIDER: missing" in message
    assert ".env.example" in message


@pytest.mark.parametrize(
    ("name", "bad_value"),
    [
        ("POSTGRES_PORT", "not-a-number"),
        ("POSTGRES_PORT", "70000"),
        ("LLM_PROVIDER", "openai"),
        ("MAX_ROWS", "0"),
        ("QUERY_TIMEOUT_SECONDS", "-5"),
    ],
)
def test_invalid_values_fail_with_their_names(
    valid_env: pytest.MonkeyPatch, name: str, bad_value: str
) -> None:
    valid_env.setenv(name, bad_value)
    with pytest.raises(ConfigError, match=name):
        load_settings(env_file=None)


def test_secrets_are_hidden_when_printed(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    text = repr(settings) + str(settings.model_dump())
    assert "admin-secret" not in text
    assert "ro-secret" not in text
    assert "gsk-secret" not in text


def test_llm_settings_have_safe_defaults(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    assert settings.llm_temperature == 0.0
    assert settings.llm_timeout_seconds == 60


@pytest.mark.parametrize(
    ("removed", "expected"),
    [
        (["GROQ_API_KEY"], "GROQ_API_KEY required when LLM_PROVIDER=groq"),
        (["GROQ_MODEL"], "GROQ_MODEL required when LLM_PROVIDER=groq"),
        (["GROQ_API_KEY", "GROQ_MODEL"], "GROQ_API_KEY and GROQ_MODEL required"),
    ],
)
def test_active_provider_needs_its_key_and_model(
    valid_env: pytest.MonkeyPatch, removed: list[str], expected: str
) -> None:
    for name in removed:
        valid_env.delenv(name)
    with pytest.raises(ConfigError, match=expected):
        load_settings(env_file=None)


def test_gemini_needs_its_own_key_and_model(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("LLM_PROVIDER", "gemini")
    with pytest.raises(ConfigError, match="GOOGLE_API_KEY and GEMINI_MODEL required"):
        load_settings(env_file=None)


def test_only_the_active_provider_is_required(valid_env: pytest.MonkeyPatch) -> None:
    # Gemini selected and configured; Groq's settings can be absent.
    valid_env.setenv("LLM_PROVIDER", "gemini")
    valid_env.setenv("GOOGLE_API_KEY", "google-secret")
    valid_env.setenv("GEMINI_MODEL", "gemini-model")
    valid_env.delenv("GROQ_API_KEY")
    valid_env.delenv("GROQ_MODEL")
    settings = load_settings(env_file=None)
    model, api_key = settings.llm_credentials()
    assert model == "gemini-model"
    assert api_key.get_secret_value() == "google-secret"


def test_llm_credentials_follow_the_active_provider(valid_env: pytest.MonkeyPatch) -> None:
    model, api_key = load_settings(env_file=None).llm_credentials()
    assert model == "groq-model"
    assert api_key.get_secret_value() == "gsk-secret"


def test_conninfo_uses_the_right_role(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)
    readonly = settings.readonly_conninfo()
    admin = settings.admin_conninfo()
    assert "user=analyst_ro" in readonly
    assert "password=ro-secret" in readonly
    assert "admin-secret" not in readonly
    assert "user=admin" in admin
    assert "port=5433" in readonly
    assert "dbname=northwind" in readonly


def test_reads_values_from_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in VALID_ENV.items()), encoding="utf-8")
    settings = load_settings(env_file=str(env_file))
    assert settings.postgres_db == "northwind"


def test_real_env_vars_override_env_file(clean_env: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in VALID_ENV.items()), encoding="utf-8")
    clean_env.setenv("MAX_ROWS", "50")
    assert load_settings(env_file=str(env_file)).max_rows == 50
