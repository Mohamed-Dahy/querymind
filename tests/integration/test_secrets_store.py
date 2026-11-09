"""The secrets store on the real database (5.7): stored encrypted, read back only by its own
workspace, refused when moved to another row, rotated to a new key, and never logged."""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg import errors
from pydantic import SecretStr
from setup_app_db import SCHEMA

from querymind.config import Settings
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.errors import SecretNotFoundError, SecretUnreadableError
from querymind.secrets.store import LocalSecretStore
from querymind.store.conversations import Owner

pytestmark = pytest.mark.integration

GROQ_KEY = SecretStr("gsk_live_0123456789abcdefghijklmnopWXYZ")
DB_PASSWORD = SecretStr("correct-horse-battery-staple")


def new_key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


@pytest.fixture
def key() -> SecretStr:
    """A key made for this test: neither .env nor CI needs SECRETS_KEY."""
    return new_key()


@pytest.fixture
def store(settings: Settings, key: SecretStr) -> LocalSecretStore:
    return LocalSecretStore(settings, cipher=SecretCipher([key]))


@pytest.fixture
def admin(settings: Settings) -> Iterator[psycopg.Connection]:
    """The admin's view of the table, to see what is really stored."""
    with psycopg.connect(settings.admin_conninfo(), autocommit=True) as conn:
        yield conn


def stored_row(admin: psycopg.Connection, secret_id: object) -> tuple[str, ...]:
    row = admin.execute(
        f"SELECT ciphertext, kind, coalesce(hint, '') FROM {SCHEMA}.secrets WHERE id = %s",
        (secret_id,),
    ).fetchone()
    assert row is not None
    return row


def test_a_saved_key_is_stored_encrypted_and_read_back(
    store: LocalSecretStore, owner: Owner, admin: psycopg.Connection, key: SecretStr
) -> None:
    info = store.save(owner.workspace_id, "llm_key", GROQ_KEY)

    ciphertext, kind, hint = stored_row(admin, info.id)
    assert GROQ_KEY.get_secret_value() not in ciphertext
    assert "gsk_" not in ciphertext
    assert (kind, hint) == ("llm_key", "WXYZ")
    # It really is the key, encrypted: it opens with our key, for this row.
    context = f"{owner.workspace_id}/{info.id}"
    opened = SecretCipher([key]).open(ciphertext, context=context)
    assert opened.get_secret_value() == GROQ_KEY.get_secret_value()

    assert store.reveal(owner.workspace_id, info.id).get_secret_value() == (
        GROQ_KEY.get_secret_value()
    )
    assert store.info(owner.workspace_id, info.id) == info


def test_a_password_shows_no_part_of_itself(
    store: LocalSecretStore, owner: Owner, admin: psycopg.Connection
) -> None:
    info = store.save(owner.workspace_id, "db_password", DB_PASSWORD)

    assert info.hint is None
    assert stored_row(admin, info.id)[2] == ""


def test_the_database_refuses_a_hint_on_a_password(owner: Owner, admin: psycopg.Connection) -> None:
    """Even a bug in the app can't store part of a password in plain text."""
    with pytest.raises(errors.CheckViolation):
        admin.execute(
            f"INSERT INTO {SCHEMA}.secrets (id, workspace_id, kind, ciphertext, hint)"
            " VALUES (%s, %s, 'db_password', 'x', 'aple')",
            (uuid4(), owner.workspace_id),
        )


def test_another_workspace_cant_see_change_or_delete_it(
    store: LocalSecretStore, owner: Owner, other_owner: Owner
) -> None:
    info = store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    other = other_owner.workspace_id

    with pytest.raises(SecretNotFoundError):
        store.reveal(other, info.id)
    with pytest.raises(SecretNotFoundError):
        store.info(other, info.id)
    with pytest.raises(SecretNotFoundError):
        store.replace(other, info.id, SecretStr("gsk_attacker_0123456789abcdefgh"))
    with pytest.raises(SecretNotFoundError):
        store.delete(other, info.id)
    # Still there, unchanged.
    assert store.reveal(owner.workspace_id, info.id).get_secret_value() == (
        GROQ_KEY.get_secret_value()
    )


def test_a_token_copied_into_another_row_is_refused(
    store: LocalSecretStore, owner: Owner, other_owner: Owner, admin: psycopg.Connection
) -> None:
    """Someone with write access to the table (but no key) moves A's key into B's row."""
    victim = store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    attacker = store.save(other_owner.workspace_id, "llm_key", SecretStr("gsk_" + "b" * 30))
    admin.execute(
        f"UPDATE {SCHEMA}.secrets SET ciphertext = %s WHERE id = %s",
        (stored_row(admin, victim.id)[0], attacker.id),
    )

    with pytest.raises(SecretUnreadableError) as refused:
        store.reveal(other_owner.workspace_id, attacker.id)
    assert GROQ_KEY.get_secret_value() not in str(refused.value)


def test_replacing_keeps_the_id_and_moves_updated_at(store: LocalSecretStore, owner: Owner) -> None:
    first = store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    newer = SecretStr("gsk_live_9876543210zyxwvutsrqponmlkABCD")

    second = store.replace(owner.workspace_id, first.id, newer)

    assert second.id == first.id and second.hint == "ABCD"
    assert second.updated_at > first.updated_at
    assert store.reveal(owner.workspace_id, first.id).get_secret_value() == (
        newer.get_secret_value()
    )


def test_a_deleted_secret_is_gone(store: LocalSecretStore, owner: Owner) -> None:
    info = store.save(owner.workspace_id, "db_password", DB_PASSWORD)

    store.delete(owner.workspace_id, info.id)

    with pytest.raises(SecretNotFoundError):
        store.reveal(owner.workspace_id, info.id)
    with pytest.raises(SecretNotFoundError):
        store.delete(owner.workspace_id, info.id)


def test_a_store_with_another_key_cant_read_it(
    store: LocalSecretStore, settings: Settings, owner: Owner
) -> None:
    info = store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    stranger = LocalSecretStore(settings, cipher=SecretCipher([new_key()]))

    with pytest.raises(SecretUnreadableError):
        stranger.reveal(owner.workspace_id, info.id)


def test_rotation_moves_every_secret_to_the_new_key(
    settings: Settings, owner: Owner, key: SecretStr
) -> None:
    old_store = LocalSecretStore(settings, cipher=SecretCipher([key]))
    info = old_store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    new = new_key()

    rotated, unreadable = LocalSecretStore(settings, cipher=SecretCipher([new, key])).rotate()

    # Other tests' (or real) secrets made with other keys are left alone and listed.
    assert rotated >= 1 and info.id not in unreadable
    new_only = LocalSecretStore(settings, cipher=SecretCipher([new]))
    assert new_only.reveal(owner.workspace_id, info.id).get_secret_value() == (
        GROQ_KEY.get_secret_value()
    )
    with pytest.raises(SecretUnreadableError):
        old_store.reveal(owner.workspace_id, info.id)


def test_nothing_secret_is_logged(
    store: LocalSecretStore, owner: Owner, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG", logger="querymind")
    info = store.save(owner.workspace_id, "llm_key", GROQ_KEY)
    store.reveal(owner.workspace_id, info.id)
    store.replace(owner.workspace_id, info.id, DB_PASSWORD)
    with pytest.raises(SecretNotFoundError):
        store.reveal(uuid4(), info.id)
    store.delete(owner.workspace_id, info.id)

    assert any(record.getMessage() == "secret saved" for record in caplog.records)
    for record in caplog.records:
        everything = str(vars(record))
        assert GROQ_KEY.get_secret_value() not in everything
        assert DB_PASSWORD.get_secret_value() not in everything
