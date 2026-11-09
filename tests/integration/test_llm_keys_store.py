"""Workspaces' own LLM keys on the real database (5.8): one per workspace, the key encrypted in
the secrets store, the old key deleted when replaced, nothing left behind when removed."""

from collections.abc import Iterator

import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from setup_app_db import SCHEMA

from querymind.config import Settings
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.errors import SecretNotFoundError
from querymind.secrets.store import LocalSecretStore
from querymind.store.conversations import Owner
from querymind.store.llm_keys import LLMKeyStore

pytestmark = pytest.mark.integration

GROQ_KEY = SecretStr("gsk_live_0123456789abcdefghijklmnopWXYZ")
CLAUDE_KEY = SecretStr("sk-ant-api03-0123456789abcdefghijABCD")


@pytest.fixture
def secrets(settings: Settings) -> LocalSecretStore:
    return LocalSecretStore(
        settings, cipher=SecretCipher([SecretStr(Fernet.generate_key().decode())])
    )


@pytest.fixture
def keys(settings: Settings, secrets: LocalSecretStore) -> LLMKeyStore:
    return LLMKeyStore(settings, secrets)


@pytest.fixture
def admin(settings: Settings) -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.admin_conninfo(), autocommit=True) as conn:
        yield conn


def secrets_of(admin: psycopg.Connection, owner: Owner) -> int:
    row = admin.execute(
        f"SELECT count(*) FROM {SCHEMA}.secrets WHERE workspace_id = %s", (owner.workspace_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_a_saved_choice_reads_back_without_the_key(
    keys: LLMKeyStore, secrets: LocalSecretStore, owner: Owner, admin: psycopg.Connection
) -> None:
    saved = keys.save(owner.workspace_id, "groq", "openai/gpt-oss-120b", GROQ_KEY)

    found = keys.get(owner.workspace_id)
    assert found == saved
    assert (found.provider, found.model, found.hint) == ("groq", "openai/gpt-oss-120b", "WXYZ")
    assert GROQ_KEY.get_secret_value() not in found.model_dump_json()
    # The key itself is in the secrets store, encrypted, and opens only server-side.
    assert secrets.reveal(owner.workspace_id, found.secret_id).get_secret_value() == (
        GROQ_KEY.get_secret_value()
    )
    stored = admin.execute(
        f"SELECT * FROM {SCHEMA}.llm_keys WHERE workspace_id = %s", (owner.workspace_id,)
    ).fetchone()
    assert GROQ_KEY.get_secret_value() not in str(stored)


def test_replacing_deletes_the_old_key(
    keys: LLMKeyStore, secrets: LocalSecretStore, owner: Owner, admin: psycopg.Connection
) -> None:
    first = keys.save(owner.workspace_id, "groq", "openai/gpt-oss-120b", GROQ_KEY)

    second = keys.save(owner.workspace_id, "anthropic", "claude-haiku-4-5", CLAUDE_KEY)

    assert second.secret_id != first.secret_id  # the version WorkspaceLLMs watches
    assert keys.get(owner.workspace_id) == second
    with pytest.raises(SecretNotFoundError):
        secrets.reveal(owner.workspace_id, first.secret_id)
    assert secrets_of(admin, owner) == 1


def test_removing_leaves_nothing_behind(
    keys: LLMKeyStore, owner: Owner, admin: psycopg.Connection
) -> None:
    keys.save(owner.workspace_id, "groq", "openai/gpt-oss-120b", GROQ_KEY)

    assert keys.delete(owner.workspace_id) is True

    assert keys.get(owner.workspace_id) is None
    assert secrets_of(admin, owner) == 0
    assert keys.delete(owner.workspace_id) is False


def test_each_workspace_sees_only_its_own(
    keys: LLMKeyStore, owner: Owner, other_owner: Owner
) -> None:
    keys.save(owner.workspace_id, "groq", "openai/gpt-oss-120b", GROQ_KEY)

    assert keys.get(other_owner.workspace_id) is None
    assert keys.delete(other_owner.workspace_id) is False
    assert keys.get(owner.workspace_id) is not None
