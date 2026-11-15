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


class OnboardingRunningError(StoreError):
    """A database's onboarding is already queued or running. Safe to show."""


class DictionaryNotFoundError(StoreError):
    """The database has no data dictionary yet (never onboarded). Safe to show."""


class DictionaryEditError(StoreError):
    """An edit names a table or column the dictionary doesn't have. Safe to show."""


class TableAccessInvalidError(StoreError):
    """A table permission names a table this database doesn't have (5.2). Safe to show."""


class MemberNotFoundError(StoreError):
    """No such member in this workspace (5.3). Safe to show."""


class AccountNotFoundError(StoreError):
    """No (enabled) account has this email, so it can't be added to a workspace (5.12). Safe to
    show."""


class AlreadyMemberError(StoreError):
    """The account is already a member of this workspace (5.12). Safe to show."""


class LastAdminError(StoreError):
    """The change would leave the workspace with no admin (5.12). Safe to show."""
