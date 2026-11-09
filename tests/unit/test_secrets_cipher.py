"""The secrets cipher (5.7): sealed values can't be read, changed, moved to another row, or opened
without a key, and key rotation keeps every secret readable."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from querymind.secrets.cipher import SecretCipher
from querymind.secrets.errors import SecretUnreadableError
from querymind.secrets.store import SecretInfo, hint_for

GROQ_KEY = SecretStr("gsk_live_0123456789abcdefghijklmnopWXYZ")
ROW = "workspace-1/secret-1"


def new_key() -> SecretStr:
    return SecretStr(Fernet.generate_key().decode())


@pytest.fixture
def cipher() -> SecretCipher:
    return SecretCipher([new_key()])


def test_a_sealed_secret_opens_again_for_its_own_row(cipher: SecretCipher) -> None:
    token = cipher.seal(GROQ_KEY, context=ROW)

    opened = cipher.open(token, context=ROW)

    assert opened.get_secret_value() == GROQ_KEY.get_secret_value()
    assert "gsk_" not in repr(opened)  # still a SecretStr: printing it shows **********


def test_the_token_holds_nothing_readable(cipher: SecretCipher) -> None:
    token = cipher.seal(GROQ_KEY, context=ROW)

    assert GROQ_KEY.get_secret_value() not in token
    assert "gsk_" not in token and ROW not in token
    # The same value sealed twice gives two different tokens (random IV): equal keys don't show.
    assert cipher.seal(GROQ_KEY, context=ROW) != token


def test_a_changed_token_is_refused(cipher: SecretCipher) -> None:
    token = cipher.seal(GROQ_KEY, context=ROW)
    middle = len(token) // 2
    tampered = token[:middle] + ("A" if token[middle] != "A" else "B") + token[middle + 1 :]

    with pytest.raises(SecretUnreadableError):
        cipher.open(tampered, context=ROW)


def test_a_token_copied_to_another_row_is_refused(cipher: SecretCipher) -> None:
    """Someone who can write the table, but has no key, moves A's key into B's row."""
    token = cipher.seal(GROQ_KEY, context="workspace-A/secret-1")

    with pytest.raises(SecretUnreadableError) as refused:
        cipher.open(token, context="workspace-B/secret-2")

    assert GROQ_KEY.get_secret_value() not in str(refused.value)


def test_another_key_cant_open_it(cipher: SecretCipher) -> None:
    token = cipher.seal(GROQ_KEY, context=ROW)

    with pytest.raises(SecretUnreadableError):
        SecretCipher([new_key()]).open(token, context=ROW)


def test_garbage_is_refused_not_crashing(cipher: SecretCipher) -> None:
    with pytest.raises(SecretUnreadableError):
        cipher.open("not a token at all", context=ROW)


def test_rotation_moves_a_secret_to_the_new_key() -> None:
    old, new = new_key(), new_key()
    token = SecretCipher([old]).seal(GROQ_KEY, context=ROW)

    # Step 1: the new key first, the old one still there: old tokens still open.
    both = SecretCipher([new, old])
    assert both.open(token, context=ROW).get_secret_value() == GROQ_KEY.get_secret_value()
    rotated = both.rotate(token)

    # Step 2: the old key removed: the rotated token opens, the old one doesn't.
    only_new = SecretCipher([new])
    assert only_new.open(rotated, context=ROW).get_secret_value() == GROQ_KEY.get_secret_value()
    with pytest.raises(SecretUnreadableError):
        only_new.open(token, context=ROW)


def test_rotating_a_token_no_key_opens_is_refused(cipher: SecretCipher) -> None:
    token = SecretCipher([new_key()]).seal(GROQ_KEY, context=ROW)

    with pytest.raises(SecretUnreadableError):
        cipher.rotate(token)


def test_a_cipher_needs_a_key() -> None:
    with pytest.raises(ValueError, match="at least one key"):
        SecretCipher([])


# --- What may be shown ---


def test_only_a_long_llm_key_shows_its_last_four_characters() -> None:
    assert hint_for("llm_key", GROQ_KEY) == "WXYZ"
    assert hint_for("llm_key", SecretStr("short-key")) is None  # 4 of 9 would be too many
    # A password never shows any part of itself, however long.
    assert hint_for("db_password", SecretStr("a-very-long-database-password-1234")) is None


def test_what_may_be_shown_has_no_room_for_the_value() -> None:
    info = SecretInfo(
        id=uuid4(), kind="llm_key", hint=hint_for("llm_key", GROQ_KEY), updated_at=datetime.now(UTC)
    )

    assert set(SecretInfo.model_fields) == {"id", "kind", "hint", "updated_at"}
    assert GROQ_KEY.get_secret_value() not in info.model_dump_json()
