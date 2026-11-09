"""Tests for querymind.config: values load, types are checked, and bad config fails clearly."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet

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
    assert settings.max_sql_attempts == 3  # the first try + up to 2 repairs


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


def test_history_is_off_without_the_app_role(valid_env: pytest.MonkeyPatch) -> None:
    settings = load_settings(env_file=None)

    assert not settings.history_enabled
    with pytest.raises(ConfigError, match="APP_DB_USER"):
        settings.app_conninfo()


def test_history_is_on_with_the_app_role(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("APP_DB_USER", "qm_app")
    valid_env.setenv("APP_DB_PASSWORD", "secret-app")

    settings = load_settings(env_file=None)

    assert settings.history_enabled
    assert "user=qm_app" in settings.app_conninfo()
    assert "secret-app" not in repr(settings)


@pytest.mark.parametrize("name", ["APP_DB_USER", "APP_DB_PASSWORD"])
def test_the_app_role_needs_both_settings(valid_env: pytest.MonkeyPatch, name: str) -> None:
    valid_env.setenv(name, "value")

    with pytest.raises(ConfigError, match="set together"):
        load_settings(env_file=None)


@pytest.mark.parametrize("user", ["ANALYST_RO_USER", "POSTGRES_ADMIN_USER"])
def test_the_app_role_cant_be_the_agents_or_the_admins(
    valid_env: pytest.MonkeyPatch, user: str
) -> None:
    valid_env.setenv("APP_DB_USER", VALID_ENV[user])
    valid_env.setenv("APP_DB_PASSWORD", "secret-app")

    with pytest.raises(ConfigError, match="role of its own"):
        load_settings(env_file=None)


# --- Logins (5.1) ---


def test_an_empty_jwt_secret_means_not_set_and_the_api_refuses(
    valid_env: pytest.MonkeyPatch,
) -> None:
    # As in .env.example: scripts and evals still load; only the API needs the secret.
    valid_env.setenv("JWT_SECRET", "")
    valid_env.setenv("APP_DB_USER", "qm_app")
    valid_env.setenv("APP_DB_PASSWORD", "app-password")
    settings = load_settings(env_file=None)

    assert settings.jwt_secret is None
    with pytest.raises(ConfigError, match="JWT_SECRET"):
        settings.require_logins()


def test_a_short_jwt_secret_is_refused(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("JWT_SECRET", "too-short")

    with pytest.raises(ConfigError, match="JWT_SECRET: Value should have at least 32"):
        load_settings(env_file=None)


def test_logins_need_the_app_role_too(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("JWT_SECRET", "x" * 40)
    settings = load_settings(env_file=None)

    with pytest.raises(ConfigError, match="APP_DB_USER"):
        settings.require_logins()
    valid_env.setenv("APP_DB_USER", "qm_app")
    valid_env.setenv("APP_DB_PASSWORD", "app-password")
    assert load_settings(env_file=None).require_logins().get_secret_value() == "x" * 40


# --- Secrets store (5.7) ---


def test_an_empty_secrets_key_means_not_set(valid_env: pytest.MonkeyPatch) -> None:
    valid_env.setenv("SECRETS_KEY", "")  # as in .env.example: scripts and evals still load
    settings = load_settings(env_file=None)

    assert settings.secrets_key is None
    with pytest.raises(ConfigError, match="SECRETS_KEY is not set"):
        settings.require_secrets_keys()


def test_secrets_key_holds_new_and_old_keys_in_order(valid_env: pytest.MonkeyPatch) -> None:
    new, old = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    valid_env.setenv("SECRETS_KEY", f" {new} , {old} ")

    keys = load_settings(env_file=None).require_secrets_keys()

    assert [key.get_secret_value() for key in keys] == [new, old]


def test_a_bad_secrets_key_is_refused_without_repeating_it(
    valid_env: pytest.MonkeyPatch,
) -> None:
    good = Fernet.generate_key().decode()
    valid_env.setenv("SECRETS_KEY", f"{good},not-a-real-key-but-secret")

    with pytest.raises(ConfigError, match=r"SECRETS_KEY: .*key 2 isn't a valid Fernet key") as bad:
        load_settings(env_file=None)

    assert "not-a-real-key-but-secret" not in str(bad.value)
    assert good not in str(bad.value)
