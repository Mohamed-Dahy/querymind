"""Errors raised by the safety layer."""


class UnsafeSqlError(Exception):
    """The SQL validator rejected a query. `reason` is plain language, safe to show the user."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
