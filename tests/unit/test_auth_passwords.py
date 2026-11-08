"""Password hashing (5.1a): never the password itself, salted, and a yes-or-no check."""

import pytest

from querymind.auth.passwords import hash_password, needs_rehash, verify_password

PASSWORD = "correct horse battery staple"


def test_the_hash_is_not_the_password_and_checks_it() -> None:
    stored = hash_password(PASSWORD)

    assert PASSWORD not in stored
    assert stored.startswith("$argon2id$")  # the algorithm and its settings travel with the hash
    assert verify_password(stored, PASSWORD)
    assert not verify_password(stored, PASSWORD + "!")


def test_the_same_password_hashes_differently_each_time() -> None:
    # A random salt per hash: equal passwords can't be spotted in the table, nor looked up.
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


@pytest.mark.parametrize("stored", [None, "", "not-a-hash", "$argon2id$garbage"])
def test_no_account_or_a_broken_hash_is_simply_no(stored: str | None) -> None:
    assert verify_password(stored, PASSWORD) is False


def test_fresh_hashes_need_no_rehash() -> None:
    assert not needs_rehash(hash_password(PASSWORD))
