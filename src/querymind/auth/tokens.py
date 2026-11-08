"""Login tokens (5.1b): a short-lived access token, and a refresh token that renews it.

    tokens = TokenService(secret, access_minutes=15)
    token, seconds = tokens.issue_access(user)   # sent as "Authorization: Bearer <token>"
    tokens.read_access(token)                    # -> AuthUser, or NotAuthenticatedError

**Access token** = a JWT signed with JWT_SECRET (HS256). It says who you are (user, workspace,
role) and when it expires (15 minutes). The API checks the signature and the time on every request,
with no database read. The web app keeps it in memory only: a script injected into the page can't
read it from storage later, and it's gone when the tab closes.

**Refresh token** = a random string in an httpOnly cookie (JavaScript can't read it), valid for 14
days. Only `/auth/refresh` reads it, to issue a new access token. It is stored only as a SHA-256
hash and **rotated** on every use: the old one is marked used. If a used one comes back, someone
copied it, so its whole family (every token since that login) is revoked (store/accounts.py).
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

import jwt
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from querymind.auth.errors import NotAuthenticatedError

Role = Literal["admin", "analyst", "viewer"]

ALGORITHM = "HS256"  # one shared secret; the API both signs and checks, so nothing asymmetric
ISSUER = "querymind"
LEEWAY_SECONDS = 10  # clocks drift a little between machines
_LOG_IN_AGAIN = "Please log in."


class AuthUser(BaseModel):
    """Who is asking: what an access token says, checked."""

    model_config = ConfigDict(frozen=True)

    user_id: UUID
    workspace_id: UUID
    role: Role
    email: str


class TokenService:
    """Signs and checks access tokens with JWT_SECRET."""

    def __init__(self, secret: SecretStr, *, access_minutes: int = 15) -> None:
        self._secret = secret
        self._lifetime = timedelta(minutes=access_minutes)

    def issue_access(self, user: AuthUser, *, now: datetime | None = None) -> tuple[str, int]:
        """A signed access token for `user`, and how many seconds it is valid."""
        issued = now or datetime.now(UTC)
        claims = {
            "iss": ISSUER,
            "sub": str(user.user_id),
            "wid": str(user.workspace_id),
            "role": user.role,
            "email": user.email,
            "typ": "access",  # never accept another kind of token as an access token
            "iat": issued,
            "exp": issued + self._lifetime,
        }
        token = jwt.encode(claims, self._secret.get_secret_value(), algorithm=ALGORITHM)
        return token, int(self._lifetime.total_seconds())

    def read_access(self, token: str) -> AuthUser:
        """Check an access token and say who it is for.

        Raises:
            NotAuthenticatedError: bad signature, expired, wrong issuer or kind, or missing claims.
        """
        try:
            claims = jwt.decode(
                token,
                self._secret.get_secret_value(),
                # A list of one: a token can't choose its own algorithm (e.g. "none").
                algorithms=[ALGORITHM],
                issuer=ISSUER,
                leeway=LEEWAY_SECONDS,
                options={"require": ["exp", "iat", "iss", "sub"]},
            )
            if claims.get("typ") != "access":
                raise NotAuthenticatedError(_LOG_IN_AGAIN)
            return AuthUser(
                user_id=claims["sub"],
                workspace_id=claims["wid"],
                role=claims["role"],
                email=claims["email"],
            )
        except (jwt.PyJWTError, KeyError, ValidationError) as exc:
            raise NotAuthenticatedError(_LOG_IN_AGAIN) from exc


def new_refresh_token() -> str:
    """A refresh token: 256 random bits, URL-safe. Only its hash is stored."""
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    """What the database keeps. A plain SHA-256 is enough here (unlike passwords): the token is
    random and long, so there is nothing to guess, and a stolen hash can't be used as a cookie."""
    return hashlib.sha256(token.encode()).hexdigest()
