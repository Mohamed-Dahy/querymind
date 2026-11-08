"""Errors raised by logins and tokens. Each message is safe to show to the user."""


class AuthError(Exception):
    """Base class for login failures."""


class NotAuthenticatedError(AuthError):
    """No valid access token (missing, expired, tampered with), or no valid refresh cookie."""


class InvalidLoginError(AuthError):
    """Wrong email or password. Deliberately the same message for both, so a login attempt
    can't tell which emails have an account."""


class EmailTakenError(AuthError):
    """Sign-up with an email that already has an account."""
