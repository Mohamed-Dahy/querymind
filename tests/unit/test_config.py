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
    "LLM_MODEL": "test-model",
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
