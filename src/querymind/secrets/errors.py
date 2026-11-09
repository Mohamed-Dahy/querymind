"""Errors raised by the secrets store. Messages are safe to show: never a secret or a token."""

from querymind.store.errors import StoreError


class SecretError(StoreError):
    """Base class for secrets store failures."""


class SecretNotFoundError(SecretError):
    """This workspace has no saved secret with that id."""


class SecretUnreadableError(SecretError):
    """A stored secret can't be decrypted: wrong key, tampered, or copied from another row."""
