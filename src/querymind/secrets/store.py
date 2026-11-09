"""Where users' secrets live (5.7): LLM keys and database passwords, encrypted at rest.

    store = LocalSecretStore(settings)
    info = store.save(workspace_id, "llm_key", SecretStr("gsk_..."))  # → id, kind, hint "…abcd"
    store.reveal(workspace_id, info.id)    # → SecretStr, server-side only (to call the provider)
    store.delete(workspace_id, info.id)

The rules:
- **Encrypted at rest.** The table holds a Fernet token, never the value (`secrets/cipher.py`).
- **Never sent back.** What the API may show is `SecretInfo`: id, kind, and for an LLM key its last
  4 characters. It has no field that could carry the value, so no route can return it by mistake.
- **Never logged.** Values stay `SecretStr` (its repr is `**********`); log lines name the id only.
- **One workspace.** Every query filters by workspace, so another workspace's id is "not found".

`SecretStore` is the interface the rest of the app uses. `LocalSecretStore` encrypts with
SECRETS_KEY from `.env` and keeps the token in the app's database; Phase 6 adds a store backed by
AWS KMS / Secrets Manager behind the same interface, and nothing else changes.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, SecretStr

from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.errors import SecretNotFoundError, SecretUnreadableError
from querymind.store.errors import StoreUnavailableError

logger = logging.getLogger(__name__)

SecretKind = Literal["llm_key", "db_password"]

# Only long LLM keys show their end: 4 characters of a 40-character key give nothing away, but 4
# of an 8-character password are half of it. Passwords show as "saved", nothing more.
HINT_LENGTH = 4
HINT_MIN_LENGTH = 20


def hint_for(kind: SecretKind, value: SecretStr) -> str | None:
    """The last 4 characters of a long LLM key, or None."""
    text = value.get_secret_value()
    if kind != "llm_key" or len(text) < HINT_MIN_LENGTH:
        return None
    return text[-HINT_LENGTH:]


class SecretInfo(BaseModel):
    """What may be shown about a saved secret. On purpose there is no field for the value."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    kind: SecretKind
    hint: str | None  # "abcd" of an LLM key ending in …abcd; None for passwords and short keys
    updated_at: datetime  # moves on when replaced (5.11 rebuilds what used the old value)


class SecretStore(Protocol):
    """Save, read (server-side only), replace and delete one workspace's secrets."""

    def save(self, workspace_id: UUID, kind: SecretKind, value: SecretStr) -> SecretInfo: ...

    def replace(self, workspace_id: UUID, secret_id: UUID, value: SecretStr) -> SecretInfo: ...

    def info(self, workspace_id: UUID, secret_id: UUID) -> SecretInfo: ...

    def reveal(self, workspace_id: UUID, secret_id: UUID) -> SecretStr: ...

    def delete(self, workspace_id: UUID, secret_id: UUID) -> None: ...


def _context(workspace_id: UUID, secret_id: UUID) -> str:
    """What a token is bound to: this workspace, this row (see secrets/cipher.py)."""
    return f"{workspace_id}/{secret_id}"


_NOT_FOUND = "No saved secret with this id."


class LocalSecretStore:
    """Secrets encrypted with SECRETS_KEY (Fernet), stored in `querymind_app.secrets`.

    Raises StoreUnavailableError when the database fails, SecretNotFoundError for an id this
    workspace doesn't have, SecretUnreadableError when a token can't be opened.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        cipher: SecretCipher | None = None,
        connect: Callable[[Settings], psycopg.Connection] = connect_app,
    ) -> None:
        self._settings = settings
        self._cipher = cipher or SecretCipher(settings.require_secrets_keys())
        self._connect = connect

    @contextmanager
    def _transaction(self) -> Iterator[psycopg.Connection]:
        try:
            with self._connect(self._settings) as conn:
                yield conn
        except (DatabaseUnavailableError, psycopg.Error) as exc:
            # A database error can quote the row it refused ("Failing row contains ..."), but the
            # only values we ever send are the token and the hint: never the plain secret.
            logger.warning("secrets store failed", extra={"detail": str(exc)})
            raise StoreUnavailableError("Saved keys aren't available right now.") from exc

    def save(self, workspace_id: UUID, kind: SecretKind, value: SecretStr) -> SecretInfo:
        """Encrypt and store a new secret. Its id is made here: the token is bound to it."""
        secret_id = uuid4()
        token = self._cipher.seal(value, context=_context(workspace_id, secret_id))
        with self._transaction() as conn:
            row = conn.execute(
                """
                INSERT INTO querymind_app.secrets (id, workspace_id, kind, ciphertext, hint)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING updated_at
                """,
                (secret_id, workspace_id, kind, token, hint_for(kind, value)),
            ).fetchone()
        assert row is not None  # INSERT ... RETURNING always gives the row
        logger.info(
            "secret saved",
            extra={"secret_id": str(secret_id), "workspace_id": str(workspace_id), "kind": kind},
        )
        return SecretInfo(id=secret_id, kind=kind, hint=hint_for(kind, value), updated_at=row[0])

    def replace(self, workspace_id: UUID, secret_id: UUID, value: SecretStr) -> SecretInfo:
        """A new value under the same id (e.g. a rotated API key). `updated_at` moves on."""
        token = self._cipher.seal(value, context=_context(workspace_id, secret_id))
        with self._transaction() as conn:
            found = conn.execute(
                "SELECT kind FROM querymind_app.secrets WHERE id = %s AND workspace_id = %s"
                " FOR UPDATE",
                (secret_id, workspace_id),
            ).fetchone()
            if found is None:
                raise SecretNotFoundError(_NOT_FOUND)
            kind: SecretKind = found[0]
            row = conn.execute(
                """
                UPDATE querymind_app.secrets
                SET ciphertext = %s, hint = %s, updated_at = now()
                WHERE id = %s AND workspace_id = %s
                RETURNING updated_at
                """,
                (token, hint_for(kind, value), secret_id, workspace_id),
            ).fetchone()
        assert row is not None  # the row is locked above, so it is still there
        logger.info(
            "secret replaced",
            extra={"secret_id": str(secret_id), "workspace_id": str(workspace_id), "kind": kind},
        )
        return SecretInfo(id=secret_id, kind=kind, hint=hint_for(kind, value), updated_at=row[0])

    def info(self, workspace_id: UUID, secret_id: UUID) -> SecretInfo:
        """What may be shown about it (never the value)."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT kind, hint, updated_at FROM querymind_app.secrets"
                " WHERE id = %s AND workspace_id = %s",
                (secret_id, workspace_id),
            ).fetchone()
        if row is None:
            raise SecretNotFoundError(_NOT_FOUND)
        return SecretInfo(id=secret_id, kind=row[0], hint=row[1], updated_at=row[2])

    def reveal(self, workspace_id: UUID, secret_id: UUID) -> SecretStr:
        """The decrypted value, for the server's own use (calling the LLM, connecting to a
        database). Never put it in a reply or a log line; keep it a SecretStr until the last call.
        """
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT ciphertext FROM querymind_app.secrets WHERE id = %s AND workspace_id = %s",
                (secret_id, workspace_id),
            ).fetchone()
        if row is None:
            raise SecretNotFoundError(_NOT_FOUND)
        try:
            return self._cipher.open(row[0], context=_context(workspace_id, secret_id))
        except SecretUnreadableError:
            logger.warning(
                "secret unreadable",
                extra={"secret_id": str(secret_id), "workspace_id": str(workspace_id)},
            )
            raise

    def delete(self, workspace_id: UUID, secret_id: UUID) -> None:
        """Remove it for good."""
        with self._transaction() as conn:
            deleted = conn.execute(
                "DELETE FROM querymind_app.secrets WHERE id = %s AND workspace_id = %s",
                (secret_id, workspace_id),
            ).rowcount
        if deleted == 0:
            raise SecretNotFoundError(_NOT_FOUND)
        logger.info(
            "secret deleted",
            extra={"secret_id": str(secret_id), "workspace_id": str(workspace_id)},
        )

    def rotate(self) -> tuple[int, list[UUID]]:
        """Re-encrypt every secret with the first key in SECRETS_KEY.

        One transaction, every row locked (FOR UPDATE), so a secret replaced meanwhile waits and
        is never overwritten with its old value. Returns how many were re-encrypted, and the ids
        no key could open (left as they are: their owners must save them again).
        """
        rotated = 0
        unreadable: list[UUID] = []
        with self._transaction() as conn:
            rows = conn.execute(
                "SELECT id, ciphertext FROM querymind_app.secrets ORDER BY id FOR UPDATE"
            ).fetchall()
            for secret_id, token in rows:
                try:
                    new_token = self._cipher.rotate(token)
                except SecretUnreadableError:
                    unreadable.append(secret_id)
                    continue
                conn.execute(
                    "UPDATE querymind_app.secrets SET ciphertext = %s WHERE id = %s",
                    (new_token, secret_id),
                )
                rotated += 1
        logger.info("secrets rotated", extra={"rotated": rotated, "unreadable": len(unreadable)})
        return rotated, unreadable
