"""Application settings, loaded once from environment variables and `.env`.

Usage:
    from querymind.config import get_settings

    settings = get_settings()
    settings.max_rows
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal, Self

from cryptography.fernet import Fernet
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
    # The app's own role, for saved conversations (data/setup_app_db.py). Optional: not set =
    # history is off, and scripts, evals and CI jobs that don't need it work without it.
    app_db_user: str | None = None
    app_db_password: SecretStr | None = None

    # --- Logins (5.1). The API needs both the app's role and JWT_SECRET; scripts and evals don't.
    # Signs the access tokens: anyone who has it can make a token for any user. 32+ characters.
    jwt_secret: SecretStr | None = Field(default=None, min_length=32)
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    # The refresh cookie. Its path is what the *browser* sees: the Vite proxy (and later the
    # production server) puts the API under /api. Secure: sent over HTTPS only (and localhost).
    auth_cookie_path: str = "/api/auth"
    auth_cookie_secure: bool = True

    # --- Secrets store (5.7): encrypts saved LLM keys and database passwords. Comma-separated
    # Fernet keys: the first encrypts, every one decrypts (so a key can be rotated). Not set = the
    # secrets store can't be used; scripts and evals don't need it.
    secrets_key: Annotated[list[SecretStr], NoDecode] | None = None

    # --- Rate limits (5.6), per process. Questions cost LLM calls on our key until BYOK (5.8). ---
    rate_questions_per_minute: int = Field(default=10, ge=1)  # per user
    rate_questions_per_day: int = Field(default=200, ge=1)  # per user
    rate_questions_total_per_day: int = Field(default=1000, ge=1)  # everyone together
    rate_reruns_per_minute: int = Field(default=30, ge=1)  # per user
    rate_logins_per_ip_per_minute: int = Field(default=10, ge=1)
    rate_logins_per_email_per_minute: int = Field(default=5, ge=1)
    rate_signups_per_ip_per_hour: int = Field(default=5, ge=1)
    # Saving an LLM key makes one test call (5.8): limited, so our server can't be used to try
    # out a list of stolen keys.
    rate_key_checks_per_hour: int = Field(default=10, ge=1)  # per user
    # Saving or re-testing a database connection (5.9) connects out to an address a user typed.
    rate_connection_checks_per_hour: int = Field(default=10, ge=1)  # per user

    # --- Users' own databases (5.9) ---
    # Development and tests only: lets a saved connection point at a private address (the Docker
    # database on 127.0.0.1) and skip TLS. Never on a shared server: it turns off the SSRF guard.
    allow_private_databases: bool = False

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
    # The evals lab (UI U5.1): the committed results, and BIRD's downloaded questions (optional:
    # only they hold BIRD's text, which isn't committed).
    evals_snapshot_path: Path = Path("evals/snapshot.json")
    bird_questions_path: Path = Path("data/raw/bird/mini_dev_postgresql.json")
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

    @field_validator("jwt_secret", mode="before")
    @classmethod
    def _empty_secret_is_unset(cls, value: object) -> object:
        """`JWT_SECRET=` (as in .env.example) means not set: scripts still run, the API won't."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("secrets_key", mode="before")
    @classmethod
    def _secret_keys_list(cls, value: object) -> object:
        """`SECRETS_KEY=new,old` → two keys; empty (as in .env.example) = not set."""
        if isinstance(value, str):
            keys = [part.strip() for part in value.split(",") if part.strip()]
            return keys or None
        return value

    @field_validator("secrets_key", mode="after")
    @classmethod
    def _secret_keys_are_fernet_keys(cls, keys: list[SecretStr] | None) -> list[SecretStr] | None:
        """Fail at start, not at the first save. The message never repeats the key itself."""
        for number, key in enumerate(keys or [], start=1):
            try:
                Fernet(key.get_secret_value())
            except ValueError:
                raise ValueError(
                    f"key {number} isn't a valid Fernet key (.env.example says how to make one)"
                ) from None
        return keys

    @field_validator("semantic_layer_paths", mode="before")
    @classmethod
    def _comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _app_role_is_its_own(self) -> Self:
        """Both or neither, and never a role the agent or the setup scripts use."""
        if (self.app_db_user is None) != (self.app_db_password is None):
            raise ValueError("APP_DB_USER and APP_DB_PASSWORD must be set together")
        if self.app_db_user in (self.analyst_ro_user, self.postgres_admin_user):
            raise ValueError("APP_DB_USER must be a role of its own, not the agent's or admin's")
        return self

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

    def require_logins(self) -> SecretStr:
        """What the API needs for logins (5.1): the app's role and JWT_SECRET. Returns the secret.

        Raises:
            ConfigError: one of them isn't set; the API refuses to start.
        """
        missing = [
            name
            for name, value in (("APP_DB_USER", self.app_db_user), ("JWT_SECRET", self.jwt_secret))
            if value is None
        ]
        if missing or self.jwt_secret is None:
            raise ConfigError(f"The API needs {' and '.join(missing)} for logins (.env.example)")
        return self.jwt_secret

    def require_secrets_keys(self) -> list[SecretStr]:
        """The keys the secrets store encrypts with (5.7), newest first.

        Raises:
            ConfigError: SECRETS_KEY isn't set.
        """
        if not self.secrets_key:
            raise ConfigError("SECRETS_KEY is not set: the secrets store needs it (.env.example)")
        return self.secrets_key

    @property
    def history_enabled(self) -> bool:
        """Whether conversations are saved: only when the app's role is configured."""
        return self.app_db_user is not None

    def app_conninfo(self) -> str:
        """Connection string for the app's role: saved conversations only, never agent queries."""
        if self.app_db_user is None or self.app_db_password is None:
            raise ConfigError("APP_DB_USER and APP_DB_PASSWORD are not set")
        return self._conninfo(self.app_db_user, self.app_db_password)

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
