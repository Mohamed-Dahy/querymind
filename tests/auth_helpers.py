"""Logins for tests (5.1).

Every route except the public ones needs a logged-in user. Tests about something else (answers,
history, the explorer) sign a fixed test user in with `signed_in(create_app(...))`, so they stay
about what they test. The login itself is tested in test_api_auth.py, without this shortcut, and
test_api_routes_protected.py checks that no route forgot to ask for a user.
"""

from uuid import UUID

from fastapi import FastAPI
from pydantic import SecretStr

from querymind.api.deps import get_current_user
from querymind.auth.tokens import AuthUser, Role, TokenService
from querymind.store.conversations import Owner

TEST_USER = AuthUser(
    user_id=UUID("11111111-1111-4111-8111-111111111111"),
    workspace_id=UUID("22222222-2222-4222-8222-222222222222"),
    role="admin",
    email="tester@example.com",
)
OTHER_USER = AuthUser(
    user_id=UUID("33333333-3333-4333-8333-333333333333"),
    workspace_id=UUID("44444444-4444-4444-8444-444444444444"),
    role="analyst",
    email="someone-else@example.com",
)
TEST_OWNER = Owner(user_id=TEST_USER.user_id, workspace_id=TEST_USER.workspace_id)
TEST_SECRET = SecretStr("a-test-secret-that-is-long-enough-for-hs256-0123456789")


def signed_in(app: FastAPI, user: AuthUser = TEST_USER) -> FastAPI:
    """The app with `user` logged in on every request (FastAPI's dependency override)."""
    app.dependency_overrides[get_current_user] = lambda: user
    return app


def make_tokens() -> TokenService:
    return TokenService(TEST_SECRET)


def user_for(owner: Owner, role: Role = "admin") -> AuthUser:
    """The logged-in user for a real (throwaway) account, for integration tests whose store
    checks the owner against the users table."""
    return AuthUser(
        user_id=owner.user_id, workspace_id=owner.workspace_id, role=role, email="test@example.com"
    )
