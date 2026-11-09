"""Sign-up, login, refresh, logout and "who am I" (5.1b).

    POST /auth/signup   {email, password}  -> 201 + access token, and the refresh cookie
    POST /auth/login    {email, password}  -> 200 + access token, and the refresh cookie
    POST /auth/refresh  (refresh cookie)   -> 200 + a new access token, and a new refresh cookie
    POST /auth/logout   (refresh cookie)   -> 204, the cookie cleared and its login revoked
    GET  /auth/me       (Bearer token)     -> who you are, in which workspace, with which role

The access token goes in the JSON body: the web app keeps it in memory and sends it as
"Authorization: Bearer …". The refresh token only ever travels in an httpOnly cookie, scoped to
these routes (see auth/tokens.py for why both). The cookie is SameSite=Strict: another site can't
make the browser send it, so a forged refresh or logout from a malicious page doesn't work.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from querymind.api import audit_trail
from querymind.api.deps import CurrentUser, get_accounts, get_audit
from querymind.api.schemas import ErrorResponse
from querymind.auth.errors import EmailTakenError, InvalidLoginError, NotAuthenticatedError
from querymind.auth.passwords import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    needs_rehash,
    verify_password,
)
from querymind.auth.tokens import Role, TokenService, hash_refresh_token, new_refresh_token
from querymind.config import Settings
from querymind.store.accounts import Account, AccountStore
from querymind.store.audit import AuditEntry, AuditStore

REFRESH_COOKIE = "qm_refresh"

router = APIRouter(prefix="/auth", tags=["auth"])
Accounts = Annotated[AccountStore, Depends(get_accounts)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class LoginRequest(BaseModel):
    email: EmailStr
    # No minimum here: a short password is simply wrong, and saying "too short" would hint.
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class WorkspaceInfo(BaseModel):
    id: UUID
    name: str


class Me(BaseModel):
    """Who is logged in."""

    id: UUID
    email: str
    role: Role = Field(description="Your role in this workspace: admin, analyst or viewer")
    workspace: WorkspaceInfo


class TokenResponse(BaseModel):
    """A new access token. The refresh token comes as an httpOnly cookie, never in the body."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Seconds until the access token expires")
    user: Me


class CookieSettings(BaseModel):
    """How the refresh cookie is set: from Settings, kept on the app with the token service."""

    path: str
    secure: bool
    days: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "CookieSettings":
        return cls(
            path=settings.auth_cookie_path,
            secure=settings.auth_cookie_secure,
            days=settings.refresh_token_days,
        )


def get_auth(request: Request) -> tuple[TokenService, CookieSettings]:
    tokens: TokenService | None = request.app.state.tokens
    cookies: CookieSettings | None = request.app.state.auth_cookie
    if tokens is None or cookies is None:
        raise NotAuthenticatedError("Logins aren't set up on this server.")
    return tokens, cookies


Auth = Annotated[tuple[TokenService, CookieSettings], Depends(get_auth)]

_BAD_LOGIN: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorResponse, "description": "Wrong email or password"},
    503: {"model": ErrorResponse, "description": "Accounts aren't available"},
}


def _me(account: Account) -> Me:
    return Me(
        id=account.user_id,
        email=account.email,
        role=account.role,
        workspace=WorkspaceInfo(id=account.workspace_id, name=account.workspace_name),
    )


def _logged_in(
    response: Response, account: Account, auth: Auth, refresh_token: str
) -> TokenResponse:
    """Put the refresh token in its cookie and the access token in the body."""
    tokens, cookies = auth
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        max_age=cookies.days * 24 * 3600,
        path=cookies.path,
        secure=cookies.secure,
        httponly=True,  # JavaScript can't read it
        samesite="strict",  # never sent by a request another site starts
    )
    access, seconds = tokens.issue_access(account.auth_user())
    return TokenResponse(access_token=access, expires_in=seconds, user=_me(account))


def _account_line(action: Literal["signup", "login"], account: Account) -> AuditEntry:
    return AuditEntry(
        action=action,
        outcome="ok",
        user_id=account.user_id,
        workspace_id=account.workspace_id,
        email=account.email,
    )


def _expiry(cookies: CookieSettings) -> datetime:
    return datetime.now(UTC) + timedelta(days=cookies.days)


@router.post(
    "/signup",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse, "description": "The email has an account"}},
)
def signup(
    body: SignupRequest, response: Response, accounts: Accounts, auth: Auth, audit: Audit
) -> TokenResponse:
    """Create an account and your own workspace (you are its admin), and log in."""
    email = body.email.lower()
    name = f"{email.split('@')[0]}'s workspace"[:100]
    try:
        account = accounts.sign_up(email, hash_password(body.password), workspace_name=name)
    except EmailTakenError:
        audit_trail.write(audit, AuditEntry(action="signup", outcome="email_taken", email=email))
        raise
    audit_trail.write(audit, _account_line("signup", account))
    refresh = new_refresh_token()
    accounts.start_session(account, hash_refresh_token(refresh), _expiry(auth[1]))
    return _logged_in(response, account, auth, refresh)


@router.post("/login", response_model=TokenResponse, responses=_BAD_LOGIN)
def login(
    body: LoginRequest, response: Response, accounts: Accounts, auth: Auth, audit: Audit
) -> TokenResponse:
    """Log in with email and password."""
    email = body.email.lower()
    found = accounts.find_login(email)
    # Checked even when there's no account (against a dummy hash), so both take the same time.
    if not verify_password(found[1] if found else None, body.password) or found is None:
        # The email as typed, and the account if there is one: many of these = someone guessing.
        line = AuditEntry(
            action="login_failed",
            outcome="invalid_login",
            email=email,
            user_id=found[0].user_id if found else None,
            workspace_id=found[0].workspace_id if found else None,
        )
        audit_trail.write(audit, line)
        raise InvalidLoginError("Wrong email or password.")
    account, stored_hash = found
    audit_trail.write(audit, _account_line("login", account))
    if needs_rehash(stored_hash):
        accounts.set_password_hash(account.user_id, hash_password(body.password))
    refresh = new_refresh_token()
    accounts.start_session(account, hash_refresh_token(refresh), _expiry(auth[1]))
    return _logged_in(response, account, auth, refresh)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    responses={401: {"model": ErrorResponse, "description": "No valid refresh cookie"}},
)
def refresh(
    response: Response,
    accounts: Accounts,
    auth: Auth,
    qm_refresh: Annotated[str | None, Cookie()] = None,
) -> TokenResponse:
    """A new access token from the refresh cookie (which is replaced too). The web app calls it on
    page load and when an access token expires."""
    if not qm_refresh:
        raise NotAuthenticatedError("Please log in.")
    new = new_refresh_token()
    account = accounts.rotate(
        hash_refresh_token(qm_refresh), hash_refresh_token(new), _expiry(auth[1])
    )
    return _logged_in(response, account, auth, new)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    accounts: Accounts,
    auth: Auth,
    audit: Audit,
    qm_refresh: Annotated[str | None, Cookie()] = None,
) -> None:
    """Log out: this login's refresh tokens are revoked and the cookie is cleared. An access token
    already issued still works until it expires (at most 15 minutes)."""
    if qm_refresh:
        ended = accounts.end_session(hash_refresh_token(qm_refresh))
        if ended is not None:
            user_id, workspace_id, email = ended
            line = AuditEntry(
                action="logout",
                outcome="ok",
                user_id=user_id,
                workspace_id=workspace_id,
                email=email,
            )
            audit_trail.write(audit, line)
    _, cookies = auth
    response.delete_cookie(
        REFRESH_COOKIE, path=cookies.path, secure=cookies.secure, httponly=True, samesite="strict"
    )


@router.get(
    "/me",
    response_model=Me,
    responses={401: {"model": ErrorResponse, "description": "Not logged in"}},
)
def me(user: CurrentUser, accounts: Accounts) -> Me:
    """Who is logged in, with the workspace's current name and your current role."""
    account = accounts.account(user.user_id, user.workspace_id)
    if account is None:  # deleted, disabled or removed since the token was issued
        raise NotAuthenticatedError("Please log in.")
    return _me(account)
