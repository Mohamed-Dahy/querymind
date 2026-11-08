"""The whole login flow over HTTP, on the real accounts tables (5.1)."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.auth import REFRESH_COOKIE, CookieSettings
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.store.accounts import AccountStore
from tests.auth_helpers import make_tokens

pytestmark = pytest.mark.integration


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: no app storage (see .env.example)")
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=AccountStore(settings),
        tokens=make_tokens(),
        auth_cookie=CookieSettings(path="/auth", secure=False, days=14),
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture
def email(settings: Settings) -> Iterator[str]:
    """A new address; its account (and workspace) is deleted after the test."""
    address = f"flow-{uuid4()}@example.com"
    yield address
    with connect_app(settings) as conn:
        conn.execute(
            """
            DELETE FROM querymind_app.workspaces WHERE id IN (
                SELECT m.workspace_id FROM querymind_app.memberships AS m
                JOIN querymind_app.users AS u ON u.id = m.user_id WHERE u.email = %s
            )
            """,
            (address,),
        )
        conn.execute("DELETE FROM querymind_app.users WHERE email = %s", (address,))


def bearer(response_json: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {response_json['access_token']}"}


def test_sign_up_use_refresh_and_log_out(api: TestClient, email: str) -> None:
    password = "a long enough password"
    signup = api.post("/auth/signup", json={"email": email, "password": password})
    assert signup.status_code == 201

    me = api.get("/auth/me", headers=bearer(signup.json()))
    assert me.json()["email"] == email and me.json()["role"] == "admin"

    login = api.post("/auth/login", json={"email": email.upper(), "password": password})
    assert login.status_code == 200
    refreshed = api.post("/auth/refresh")
    assert refreshed.status_code == 200
    assert api.get("/auth/me", headers=bearer(refreshed.json())).status_code == 200

    assert api.post("/auth/logout").status_code == 204
    assert REFRESH_COOKIE not in api.cookies
    assert api.post("/auth/refresh").status_code == 401


def test_a_wrong_password_and_a_second_sign_up_are_refused(api: TestClient, email: str) -> None:
    api.post("/auth/signup", json={"email": email, "password": "a long enough password"})

    wrong = api.post("/auth/login", json={"email": email, "password": "not the password"})
    again = api.post("/auth/signup", json={"email": email, "password": "another long password"})

    assert wrong.status_code == 401 and wrong.json()["error"] == "invalid_login"
    assert again.status_code == 409 and again.json()["error"] == "email_taken"
