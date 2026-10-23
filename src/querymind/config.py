"""Application settings, loaded once from environment variables and `.env`.

Usage:
    from querymind.config import get_settings

    settings = get_settings()
    settings.max_rows
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


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
    # How many SQL queries the agent may try per question: the first + repairs after an error.
    max_sql_attempts: int = Field(default=3, ge=1, le=5)

    # --- Semantic layer (3.1, 3.2) ---
    # Business definitions shown to the LLM (metrics, synonyms, joins), one file per part of the
    # database. In .env: comma-separated, relative to where the app starts (the repo root).
    # Empty = none. NoDecode: read the env value as plain text, not as JSON.
    semantic_layer_paths: Annotated[list[Path], NoDecode] = [
        Path("semantic_layer/northwind.yaml"),
        Path("semantic_layer/chinook.yaml"),
    ]

    # --- Schema retrieval (3.3) ---
    # Send the LLM only the tables a question needs. auto = only when the database has more than
    # RETRIEVAL_MIN_TABLES tables; on = always (needs GOOGLE_API_KEY and the index built by
    # scripts/build_index.py); off = always the whole schema.
    schema_retrieval: Literal["auto", "on", "off"] = "auto"
    retrieval_min_tables: int = Field(default=15, ge=0)
    retrieval_top_k: int = Field(default=5, ge=1, le=25)  # tables the search picks
    retrieval_max_tables: int = Field(default=8, ge=1, le=50)  # incl. follow-ups and join paths
    # How tables are ranked (3.4): vector (meaning) · keyword (exact words, Postgres full-text) ·
    # hybrid (both, fused by rank) · hybrid_rerank (hybrid + one LLM call reorders the top 10).
    retrieval_mode: Literal["vector", "keyword", "hybrid", "hybrid_rerank"] = "hybrid"
    # Few-shot examples (3.5): how many verified question → SQL pairs (semantic_layer/
    # <db>.examples.yaml) to show, the closest to the question. 0 = none. They come with
    # schema retrieval (same embedding, same index): with the whole schema there are none.
    few_shot_examples: int = Field(default=3, ge=0, le=10)
    # Query rewriting (3.6): a follow-up ("who looks after them?") is also rewritten into a
    # standalone question by one small LLM call, and both are searched. Only with history and
    # schema retrieval; the SQL prompt still gets the original question + the history. Off by
    # default: carry-over already got every follow-up its tables (ADR 005).
    query_rewriting: bool = False
    # Embeddings always use Google (GOOGLE_API_KEY), whatever LLM_PROVIDER is.
    embedding_model: str = "gemini-embedding-001"

    @field_validator("semantic_layer_paths", mode="before")
    @classmethod
    def _comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

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
