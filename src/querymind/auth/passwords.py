"""Password hashing with Argon2id (5.1).

    stored = hash_password("correct horse battery")   # what goes in the database
    verify_password(stored, "correct horse battery")  # True

A password is never stored, only its hash. Argon2id is slow and memory-hungry on purpose: one check
takes a fraction of a second for us, but someone who steals the hashes can only try a few guesses a
second per core, instead of billions with a plain hash like SHA-256. Each hash has its own random
salt, so two users with the same password get different hashes.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# Long enough to resist guessing; the upper bound stops a megabyte "password" from costing us a
# slow hash on every login attempt.
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128

# The library's defaults are RFC 9106's recommended settings for Argon2id.
_hasher = PasswordHasher()

# Checked when the email has no account, so a login takes as long either way: response time
# can't reveal which emails exist.
_DUMMY_HASH = _hasher.hash("not a real password, only here to take the same time")


def hash_password(password: str) -> str:
    """The Argon2id hash to store (it includes its own salt and settings)."""
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    """Whether `password` matches. None = no such account: still spends the time of a check."""
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    """True when the hash was made with older settings: re-hash it at the next good login."""
    return _hasher.check_needs_rehash(stored_hash)
