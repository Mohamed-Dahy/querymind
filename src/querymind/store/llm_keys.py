"""Each workspace's own LLM: provider, model and key (5.8, ADR 010).

    keys = LLMKeyStore(settings, secrets)
    keys.save(workspace_id, "groq", "openai/gpt-oss-120b", SecretStr("gsk_..."))
    keys.get(workspace_id)      # → provider, model, secret id, hint "…abcd" (never the key)
    keys.delete(workspace_id)   # back to the demo key

One per workspace. The key itself lives in the secrets store (5.7); this table only points to it.
Saving makes a **new** secret every time and then deletes the old one, so the secret id changes
with every save: `WorkspaceLLMs` uses it to know when a cached provider is out of date.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

import psycopg
from psycopg.rows import class_row
from pydantic import BaseModel, ConfigDict, SecretStr

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.llm.catalog import ProviderId
from querymind.secrets.errors import SecretNotFoundError
from querymind.secrets.store import SecretStore
from querymind.store.errors import StoreError, StoreUnavailableError

logger = logging.getLogger(__name__)


class LLMKeyRecord(BaseModel):
    """A workspace's LLM choice, without the key."""

    model_config = ConfigDict(frozen=True)

    provider: ProviderId
    model: str
    secret_id: UUID  # changes on every save: the version of this choice
    hint: str | None  # the key's last 4 characters (from the secrets store)
    updated_at: datetime


class LLMKeyStore:
    """Reads and writes `querymind_app.llm_keys`; the key goes to the secrets store."""

    def __init__(
        self,
        settings: Settings,
        secrets: SecretStore,
        *,
        connect: Callable[[Settings], psycopg.Connection] = connect_app,
    ) -> None:
        self._settings = settings
        self._secrets = secrets
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            logger.warning("llm key store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Saved keys aren't available right now.") from exc

    def get(self, workspace_id: UUID) -> LLMKeyRecord | None:
        """The workspace's own LLM, or None (it uses the demo key)."""
        with self._transaction() as conn:
            return (
                conn.cursor(row_factory=class_row(LLMKeyRecord))
                .execute(
                    """
                    SELECT k.provider, k.model, k.secret_id, s.hint, k.updated_at
                    FROM querymind_app.llm_keys AS k
                    JOIN querymind_app.secrets AS s ON s.id = k.secret_id
                    WHERE k.workspace_id = %s
                    """,
                    (workspace_id,),
                )
                .fetchone()
            )

    def save(
        self, workspace_id: UUID, provider: ProviderId, model: str, api_key: SecretStr
    ) -> LLMKeyRecord:
        """Save (or replace) the workspace's LLM. Check the key first (llm/key_check.py)."""
        # 1. The new key, encrypted, under a new id.
        info = self._secrets.save(workspace_id, "llm_key", api_key)
        try:
            # 2. Point the workspace at it (and learn which secret it pointed to before).
            with self._transaction() as conn:
                old = conn.execute(
                    "SELECT secret_id FROM querymind_app.llm_keys WHERE workspace_id = %s"
                    " FOR UPDATE",
                    (workspace_id,),
                ).fetchone()
                row = conn.execute(
                    """
                    INSERT INTO querymind_app.llm_keys (workspace_id, provider, model, secret_id)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (workspace_id) DO UPDATE
                    SET provider = excluded.provider, model = excluded.model,
                        secret_id = excluded.secret_id, updated_at = now()
                    RETURNING updated_at
                    """,
                    (workspace_id, provider, model, info.id),
                ).fetchone()
        except StoreError:
            self._forget(workspace_id, info.id)  # no orphan secret left behind
            raise
        assert row is not None  # INSERT ... RETURNING always gives the row
        # 3. The old key is no longer used: delete it.
        if old is not None:
            self._forget(workspace_id, old[0])
        logger.info(
            "llm key saved",
            extra={"workspace_id": str(workspace_id), "provider": provider, "model": model},
        )
        return LLMKeyRecord(
            provider=provider, model=model, secret_id=info.id, hint=info.hint, updated_at=row[0]
        )

    def delete(self, workspace_id: UUID) -> bool:
        """Remove the workspace's own LLM (and its key). False when it had none."""
        record = self.get(workspace_id)
        if record is None:
            return False
        # Deleting the secret deletes this row too (ON DELETE CASCADE).
        try:
            self._secrets.delete(workspace_id, record.secret_id)
        except SecretNotFoundError:
            return False  # deleted (or replaced) by another request meanwhile
        logger.info("llm key deleted", extra={"workspace_id": str(workspace_id)})
        return True

    def _forget(self, workspace_id: UUID, secret_id: UUID) -> None:
        """Delete a secret nothing points to. Best effort: a failure is logged, not raised."""
        try:
            self._secrets.delete(workspace_id, secret_id)
        except StoreError:
            logger.warning(
                "old llm key not deleted",
                extra={"workspace_id": str(workspace_id), "secret_id": str(secret_id)},
            )
