"""The login routes over HTTP (5.1b), with an in-memory account store.

What the routes do: status codes, the error shapes, the cookie's flags, the token in the body.
Rotation and reuse detection on the real database: tests/integration/test_accounts_store.py.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.auth import REFRESH_COOKIE, CookieSettings
from querymind.api.main import create_app
from querymind.auth.errors import EmailTakenError, NotAuthenticatedError
from querymind.auth.passwords import hash_password
from querymind.store.accounts import Account, Membership
from querymind.store.errors import AccountsUnavailableError, MemberNotFoundError
from tests.auth_helpers import make_tokens

PASSWORD = "a long enough password"
COOKIES = CookieSettings(path="/auth", secure=False, days=14)  # http://testserver, no /api proxy


class FakeAccounts:
    """Accounts in dicts. Refresh tokens: used once, then refused (no grace, no families)."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.users: dict[str, tuple[Account, str]] = {}
        self.sessions: dict[str, Account] = {}  # live refresh token hash -> account
        self.ended: list[str] = []
        self.joined: dict[UUID, list[Account]] = {}  # other workspaces a user was added to (5.12)

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    def sign_up(self, email: str, password_hash: str, *, workspace_name: str) -> Account:
        self._check()
        if email in self.users:
            raise EmailTakenError("An account with this email already exists.")
        account = Account(
            user_id=uuid4(),
            email=email,
            workspace_id=uuid4(),
            workspace_name=workspace_name,
            role="admin",
        )
        self.users[email] = (account, password_hash)
        return account

    def find_login(self, email: str) -> tuple[Account, str] | None:
        self._check()
        return self.users.get(email)

    def _all(self, user_id: Any) -> list[Account]:
        own = [a for a, _ in self.users.values() if a.user_id == user_id]
        return own + self.joined.get(user_id, [])

    def account(self, user_id: Any, workspace_id: Any) -> Account | None:
        return next((a for a in self._all(user_id) if a.workspace_id == workspace_id), None)

    def workspaces(self, user_id: Any) -> list[Membership]:
        return [
            Membership(workspace_id=a.workspace_id, workspace_name=a.workspace_name, role=a.role)
            for a in self._all(user_id)
        ]

    def switch(
        self,
        token_hash: str,
        new_hash: str,
        expires_at: datetime,
        *,
        user_id: Any,
        workspace_id: Any,
    ) -> Account:
        account = self.sessions.get(token_hash)
        if account is None or account.user_id != user_id:
            raise NotAuthenticatedError("Please log in.")
        target = self.account(user_id, workspace_id)
        if target is None:
            raise MemberNotFoundError("You aren't a member of that workspace.")
        del self.sessions[token_hash]
        self.sessions[new_hash] = target
        return target

    def set_password_hash(self, user_id: Any, password_hash: str) -> None: ...

    def start_session(self, account: Account, token_hash: str, expires_at: datetime) -> None:
        self.sessions[token_hash] = account

    def rotate(self, token_hash: str, new_hash: str, expires_at: datetime) -> Account:
        account = self.sessions.pop(token_hash, None)
        if account is None:
            raise NotAuthenticatedError("Please log in.")
        self.sessions[new_hash] = account
        return account

    def end_session(self, token_hash: str) -> None:
        self.ended.append(token_hash)
        self.sessions.pop(token_hash, None)


def client(accounts: FakeAccounts) -> TestClient:
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        tokens=make_tokens(),
        auth_cookie=COOKIES,
    )
    return TestClient(app, raise_server_exceptions=False)


def signed_up(accounts: FakeAccounts, email: str = "ana@example.com") -> None:
    account = Account(
        user_id=uuid4(),
        email=email,
        workspace_id=uuid4(),
        workspace_name="Ana's workspace",
        role="analyst",
    )
    accounts.users[email] = (account, hash_password(PASSWORD))


def test_sign_up_creates_an_admin_and_logs_in() -> None:
    accounts = FakeAccounts()
    with client(accounts) as api:
        response = api.post("/auth/signup", json={"email": "Ana@Example.com", "password": PASSWORD})

        assert response.status_code == 201
        body = response.json()
        assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
        assert body["user"]["email"] == "ana@example.com"  # one account per address, any case
        assert body["user"]["role"] == "admin"
        assert body["user"]["workspace"]["name"] == "ana's workspace"
        stored_hash = accounts.users["ana@example.com"][1]
        assert PASSWORD not in stored_hash  # only the hash is kept
        # The token in the body opens the API.
        me = api.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.json()["email"] == "ana@example.com"


def test_the_refresh_cookie_is_locked_down() -> None:
    with client(FakeAccounts()) as api:
        response = api.post("/auth/signup", json={"email": "ana@example.com", "password": PASSWORD})

    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{REFRESH_COOKIE}=")
    assert "HttpOnly" in cookie  # JavaScript can't read it
    assert "SameSite=strict" in cookie  # another site can't make the browser send it
    assert "Path=/auth" in cookie  # only the login routes ever get it
    assert REFRESH_COOKIE not in response.json()["access_token"]  # never in the body


def test_the_cookie_is_https_only_when_secure() -> None:
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=FakeAccounts(),  # type: ignore[arg-type]
        tokens=make_tokens(),
        auth_cookie=CookieSettings(path="/api/auth", secure=True, days=14),
    )
    with TestClient(app) as api:
        response = api.post("/auth/signup", json={"email": "ana@example.com", "password": PASSWORD})

    assert "Secure" in response.headers["set-cookie"]
    assert "Path=/api/auth" in response.headers["set-cookie"]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"email": "not-an-email", "password": PASSWORD}, "email"),
        ({"email": "ana@example.com", "password": "short"}, "password"),
        ({"email": "ana@example.com", "password": "x" * 129}, "password"),
    ],
)
def test_sign_up_checks_its_input(body: dict[str, str], field: str) -> None:
    with client(FakeAccounts()) as api:
        response = api.post("/auth/signup", json=body)

    assert response.status_code == 422
    assert response.json()["message"].startswith(field)


def test_an_email_signs_up_once() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        response = api.post("/auth/signup", json={"email": "ana@example.com", "password": PASSWORD})

    assert response.status_code == 409
    assert response.json()["error"] == "email_taken"


def test_login_with_the_right_password() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        response = api.post("/auth/login", json={"email": "ANA@example.com", "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["user"]["role"] == "analyst"
    assert REFRESH_COOKIE in response.cookies


@pytest.mark.parametrize(
    "email", ["ana@example.com", "nobody@example.com"], ids=["wrong password", "no account"]
)
def test_a_failed_login_never_says_which_part_was_wrong(email: str) -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        response = api.post("/auth/login", json={"email": email, "password": "wrong password!"})

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_login", "message": "Wrong email or password."}
    assert REFRESH_COOKIE not in response.cookies


def test_refresh_gives_a_new_token_and_a_new_cookie() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        api.post("/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
        first = api.cookies[REFRESH_COOKIE]

        response = api.post("/auth/refresh")

        assert response.status_code == 200
        assert response.json()["user"]["email"] == "ana@example.com"
        assert api.cookies[REFRESH_COOKIE] != first  # rotated
        # The old cookie is spent.
        api.cookies.set(REFRESH_COOKIE, first, path="/auth")
        again = api.post("/auth/refresh")
        assert again.status_code == 401 and again.json()["error"] == "not_authenticated"


def test_refresh_without_a_cookie_is_a_401() -> None:
    with client(FakeAccounts()) as api:
        response = api.post("/auth/refresh")

    assert response.status_code == 401
    assert response.json()["error"] == "not_authenticated"


def test_logout_ends_the_login_and_clears_the_cookie() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        api.post("/auth/login", json={"email": "ana@example.com", "password": PASSWORD})

        response = api.post("/auth/logout")

        assert response.status_code == 204
        assert len(accounts.ended) == 1
        assert REFRESH_COOKIE not in api.cookies
        assert api.post("/auth/refresh").status_code == 401


def test_me_needs_a_token() -> None:
    with client(FakeAccounts()) as api:
        response = api.get("/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_a_failing_account_store_is_a_clean_503() -> None:
    accounts = FakeAccounts(error=AccountsUnavailableError("Accounts aren't available right now."))
    with client(accounts) as api:
        response = api.post("/auth/login", json={"email": "ana@example.com", "password": PASSWORD})

    assert response.status_code == 503
    assert response.json() == {
        "error": "accounts_unavailable",
        "message": "Accounts aren't available right now.",
    }


def test_without_logins_set_up_the_routes_say_so() -> None:
    app = create_app(pipeline=object())  # type: ignore[arg-type]
    with TestClient(app, raise_server_exceptions=False) as api:
        response = api.post("/auth/login", json={"email": "ana@example.com", "password": PASSWORD})

    assert response.status_code == 503
    assert response.json()["error"] == "accounts_unavailable"


# --- Several workspaces (5.12): the list and the switch ---


def joined(accounts: FakeAccounts, email: str, role: Any = "viewer") -> Account:
    """`email`'s user, added to someone else's workspace with `role`."""
    own = accounts.users[email][0]
    other = own.model_copy(
        update={"workspace_id": uuid4(), "workspace_name": "Bob's workspace", "role": role}
    )
    accounts.joined.setdefault(own.user_id, []).append(other)
    return other


def logged_in(api: TestClient, email: str = "ana@example.com") -> dict[str, str]:
    body = api.post("/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_workspaces_lists_yours_first_and_marks_the_current_one() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    bobs = joined(accounts, "ana@example.com")
    with client(accounts) as api:
        response = api.get("/auth/workspaces", headers=logged_in(api))

    assert response.status_code == 200
    rows = response.json()["workspaces"]
    assert [(w["name"], w["role"], w["current"]) for w in rows] == [
        ("Ana's workspace", "analyst", True),
        ("Bob's workspace", "viewer", False),
    ]
    assert rows[1]["id"] == str(bobs.workspace_id)


def test_switching_moves_the_login_and_a_reload_stays_there() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    bobs = joined(accounts, "ana@example.com")
    with client(accounts) as api:
        switched = api.post(
            "/auth/switch", json={"workspace_id": str(bobs.workspace_id)}, headers=logged_in(api)
        )
        assert switched.status_code == 200
        assert REFRESH_COOKIE in switched.headers["set-cookie"]  # the login moved too
        body = switched.json()
        me = api.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
        reloaded = api.post("/auth/refresh")  # what a page reload does

    assert body["user"]["workspace"]["name"] == "Bob's workspace"
    assert body["user"]["role"] == "viewer"
    assert me.json()["workspace"]["id"] == str(bobs.workspace_id)
    assert reloaded.json()["user"]["workspace"]["id"] == str(bobs.workspace_id)


def test_switching_to_a_workspace_you_arent_in_is_a_404_and_changes_nothing() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    with client(accounts) as api:
        headers = logged_in(api)
        response = api.post("/auth/switch", json={"workspace_id": str(uuid4())}, headers=headers)
        still = api.post("/auth/refresh")

    assert (response.status_code, response.json()["error"]) == (404, "member_not_found")
    assert still.status_code == 200
    assert still.json()["user"]["workspace"]["name"] == "Ana's workspace"


def test_switching_needs_the_refresh_cookie_and_a_token() -> None:
    accounts = FakeAccounts()
    signed_up(accounts)
    bobs = joined(accounts, "ana@example.com")
    body = {"workspace_id": str(bobs.workspace_id)}
    with client(accounts) as api:
        headers = logged_in(api)
        no_token = api.post("/auth/switch", json=body)
        api.cookies.clear()
        no_cookie = api.post("/auth/switch", json=body, headers=headers)

    assert no_token.status_code == no_cookie.status_code == 401
