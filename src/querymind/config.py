"""Application settings, loaded once from environment variables and `.env`.

Usage:
    from querymind.config import get_settings

    settings = get_settings()
    settings.max_rows
"""

from functools import lru_cache
from typing import Literal, Self

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(Exception):
    """Required settings are missing or invalid."""


# (API key env var, model env var) each LLM provider needs.
_LLM_ENV_VARS = {
    "groq": ("GROQ_API_KEY", "GROQ_MODEL"),
    "gemini": ("GOOGLE_API_KEY", "GEMINI_MODEL"),
}


class Settings(BaseSettings):
    """Every setting QueryMind needs. Each field maps to an env var of the same name in CAPS."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # .env may hold variables for other tools (e.g. Docker Compose)
    )

    # --- Database ---
    postgres_host: str
    postgres_port: int = Field(gt=0, le=65535)
    postgres_db: str
    # Superuser: setup and seeding only. The agent must never connect with it.
    postgres_admin_user: str
    postgres_admin_password: SecretStr
    # Read-only role the agent connects with.
    analyst_ro_user: str
    analyst_ro_password: SecretStr

    # --- LLM (Bedrock is added in Phase 6) ---
    llm_provider: Literal["groq", "gemini"]
    # 0 = most predictable output, which is what we want for SQL. (Gemini 3.x ignores it.)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: int = Field(default=60, gt=0, le=600)
    # Each provider has its own key + model; only the active provider's pair is required.
    groq_api_key: SecretStr | None = None
    groq_model: str | None = None
    google_api_key: SecretStr | None = None
    gemini_model: str | None = None

    # --- Query limits ---
    query_timeout_seconds: int = Field(default=10, gt=0, le=300)
    max_rows: int = Field(default=1000, gt=0, le=100_000)

    @model_validator(mode="after")
    def _active_provider_is_configured(self) -> Self:
        """Runs after all fields load: the chosen LLM provider must have its key and model."""
        missing = self._missing_llm_settings()
        if missing:
            raise ValueError(
                f"{' and '.join(missing)} required when LLM_PROVIDER={self.llm_provider}"
            )
        return self

    def llm_credentials(self) -> tuple[str, SecretStr]:
        """Model name and API key of the active provider (LLM_PROVIDER)."""
        model, api_key = self._llm_pair()
        # Validation already guarantees both; this check also tells mypy they aren't None.
        if model is None or api_key is None:
            raise ConfigError(f"{' and '.join(self._missing_llm_settings())} not set")
        return model, api_key

    def _llm_pair(self) -> tuple[str | None, SecretStr | None]:
        match self.llm_provider:
            case "groq":
                return self.groq_model, self.groq_api_key
            case "gemini":
                return self.gemini_model, self.google_api_key

    def _missing_llm_settings(self) -> list[str]:
        """Env var names the active provider needs but that aren't set."""
        model, api_key = self._llm_pair()
        key_var, model_var = _LLM_ENV_VARS[self.llm_provider]
        missing = []
        if api_key is None:
            missing.append(key_var)
        if model is None:
            missing.append(model_var)
        return missing

    def readonly_conninfo(self) -> str:
        """Connection string for the read-only role. This is what the agent uses."""
        return self._conninfo(self.analyst_ro_user, self.analyst_ro_password)

    def admin_conninfo(self) -> str:
        """Connection string for the admin role. Setup scripts only."""
        return self._conninfo(self.postgres_admin_user, self.postgres_admin_password)

    def _conninfo(self, user: str, password: SecretStr) -> str:
        # make_conninfo quotes each value correctly, even passwords with spaces or quotes.
        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=user,
            password=password.get_secret_value(),
            connect_timeout=10,
        )


def load_settings(env_file: str | None = ".env") -> Settings:
    """Build Settings, turning pydantic's validation error into one clear message.

    Pass `env_file=None` to read only real environment variables (used by tests).
    """
    try:
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise ConfigError(_describe(exc)) from None


@lru_cache
def get_settings() -> Settings:
    """Return the settings, loaded once and then reused (fail fast on first call)."""
    return load_settings()


def _describe(exc: ValidationError) -> str:
    """List each bad setting by its env var name, e.g. 'POSTGRES_PORT: Input should be ...'."""
    lines = []
    for error in exc.errors():
        name = str(error["loc"][0]).upper() if error["loc"] else "SETTINGS"
        reason = "missing" if error["type"] == "missing" else error["msg"]
        lines.append(f"  - {name}: {reason}")
    return (
        "Invalid QueryMind configuration:\n"
        + "\n".join(lines)
        + "\nSet these in .env (see .env.example) or as environment variables."
    )
