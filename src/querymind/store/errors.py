"""Errors raised by the conversation store. Callers catch these, never raw psycopg errors."""


class StoreError(Exception):
    """Base class for all store failures."""


class StoreUnavailableError(StoreError):
    """The store could not be reached or could not do the work. The message is safe to show."""


class ConversationNotFoundError(StoreError):
    """No conversation has this id. The message is safe to show."""


class TurnNotFoundError(StoreError):
    """The conversation has no such turn, or it has no SQL to run. The message is safe to show."""


class AccountsUnavailableError(StoreUnavailableError):
    """The accounts tables could not be reached. The message is safe to show."""


class ConnectionNotFoundError(StoreError):
    """This workspace has no database connection with that id. The message is safe to show."""


class ConnectionNameTakenError(StoreError):
    """The workspace already has a connection with this name. The message is safe to show."""


class ConversationOtherDatabaseError(StoreError):
    """A follow-up asked on another database than its conversation's. Safe to show."""


class ConnectionUnreadableError(StoreError):
    """A saved database's password can't be decrypted: save the database again. Safe to show."""
