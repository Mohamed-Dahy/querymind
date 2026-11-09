"""Encrypt and decrypt one secret, bound to the row it belongs to (5.7).

    cipher = SecretCipher([new_key, old_key])
    token = cipher.seal("gsk_live_abc", context="<workspace id>/<secret id>")
    cipher.open(token, context="<workspace id>/<secret id>")   # → "gsk_live_abc"
    cipher.open(token, context="<another row>")                # → SecretUnreadableError

**Fernet** (from the `cryptography` package) is "authenticated encryption": AES-128 in CBC mode
for secrecy plus an HMAC-SHA256 tag for integrity, with a random IV and a timestamp in every token.
Changing a single byte of the token makes `decrypt` fail, so a tampered value is refused, never
silently turned into garbage. **MultiFernet** holds several keys: the first encrypts, any of them
decrypts, and `rotate` re-encrypts a token with the first. That is how a key is replaced.

**The context.** Fernet can't bind extra data to a token (no "associated data"). So the row's
identity goes *inside* the encrypted payload and is checked on the way out. Without it, someone who
can write the table (but has no key) could copy workspace A's encrypted key into workspace B's row,
and B's questions would run on A's key and bill. With it, that copy fails to open.
"""

import json

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from pydantic import SecretStr

from querymind.secrets.errors import SecretUnreadableError

_UNREADABLE = "A saved secret can't be read: save it again."


class SecretCipher:
    """Seals and opens secrets with a list of Fernet keys (newest first)."""

    def __init__(self, keys: list[SecretStr]) -> None:
        if not keys:
            raise ValueError("at least one key is needed")
        self._fernet = MultiFernet([Fernet(key.get_secret_value()) for key in keys])

    def seal(self, value: SecretStr, *, context: str) -> str:
        """Encrypt `value` for the row named by `context`. Returns text (URL-safe base64)."""
        payload = json.dumps({"ctx": context, "value": value.get_secret_value()})
        return self._fernet.encrypt(payload.encode()).decode()

    def open(self, token: str, *, context: str) -> SecretStr:
        """Decrypt a token sealed for `context`.

        Raises:
            SecretUnreadableError: no key opens it, it was changed, or it belongs to another row.
        """
        try:
            payload = json.loads(self._fernet.decrypt(token.encode()))
        except (InvalidToken, ValueError):  # ValueError: not even valid JSON inside
            raise SecretUnreadableError(_UNREADABLE) from None
        if not isinstance(payload, dict) or payload.get("ctx") != context:
            # Decrypted fine, but it was sealed for another row: refuse it.
            raise SecretUnreadableError(_UNREADABLE)
        return SecretStr(str(payload["value"]))

    def rotate(self, token: str) -> str:
        """The same secret, re-encrypted with the first (newest) key. Context stays inside.

        Raises:
            SecretUnreadableError: no key opens it.
        """
        try:
            return self._fernet.rotate(token.encode()).decode()
        except InvalidToken:
            raise SecretUnreadableError(_UNREADABLE) from None
