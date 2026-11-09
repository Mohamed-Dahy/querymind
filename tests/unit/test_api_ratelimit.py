"""Rate limits over HTTP (5.6): a clear 429 with Retry-After, before any work is done."""

import time
from typing import Any

from fastapi.testclient import TestClient

from querymind.api.main import create_app
from querymind.api.ratelimit import RateLimits
from querymind.store.accounts import Account
from tests.auth_helpers import TEST_USER, make_tokens
from tests.unit.test_api import FakePipeline
from tests.unit.test_api_auth import COOKIES, PASSWORD, FakeAccounts
from tests.unit.test_api_conversations import ID
from tests.unit.test_api_conversations import FakePipeline as FakeRerunPipeline
from tests.unit.test_api_conversations import FakeStore as FakeConversationStore
from tests.unit.test_api_saving import FakePipeline as FakeStreamingPipeline

TOKENS = make_tokens()
BEARER = {"Authorization": f"Bearer {TOKENS.issue_access(TEST_USER)[0]}"}


def limits(**changes: int) -> RateLimits:
    settings = {
        "questions_per_minute": 2,
        "questions_per_day": 100,
        "questions_total_per_day": 100,
        "reruns_per_minute": 1,
        "logins_per_ip_per_minute": 100,
        "logins_per_email_per_minute": 2,
        "signups_per_ip_per_hour": 1,
    } | changes
    return RateLimits(**settings, clock=time.monotonic)


def client(**parts: Any) -> TestClient:
    app = create_app(tokens=TOKENS, auth_cookie=COOKIES, **parts)
    return TestClient(app, raise_server_exceptions=False)


def test_the_third_question_in_a_minute_waits() -> None:
    pipeline = FakePipeline()
    with client(pipeline=pipeline, limits=limits()) as api:
        statuses = [
            api.post("/ask", json={"question": f"q{n}"}, headers=BEARER).status_code
            for n in range(2)
        ]
        response = api.post("/ask", json={"question": "q3"}, headers=BEARER)

    assert statuses == [200, 200]
    assert response.status_code == 429
    body = response.json()
    assert body["error"] == "rate_limited"
    assert body["retry_after_seconds"] == 60
    assert response.headers["Retry-After"] == "60"
    assert pipeline.questions == ["q0", "q1"]  # the third never reached the LLM


def test_the_stream_answers_a_plain_429_before_it_starts() -> None:
    with client(pipeline=FakeStreamingPipeline(), limits=limits(questions_per_minute=1)) as api:
        api.post("/ask/stream", json={"question": "q"}, headers=BEARER)
        response = api.post("/ask/stream", json={"question": "q"}, headers=BEARER)

    assert response.status_code == 429
    assert response.json()["error"] == "rate_limited"


def test_not_logged_in_is_a_401_and_uses_up_nothing() -> None:
    with client(pipeline=FakePipeline(), limits=limits()) as api:
        for _ in range(5):
            assert api.post("/ask", json={"question": "q"}).status_code == 401
        ok = [
            api.post("/ask", json={"question": "q"}, headers=BEARER).status_code for _ in range(2)
        ]

    assert ok == [200, 200]


def test_reruns_have_their_own_limit() -> None:
    parts = {"pipeline": FakeRerunPipeline(), "store": FakeConversationStore()}
    with client(**parts, limits=limits()) as api:
        first = api.post(f"/conversations/{ID}/turns/1/rerun", headers=BEARER)
        second = api.post(f"/conversations/{ID}/turns/1/rerun", headers=BEARER)

    assert (first.status_code, second.status_code) == (200, 429)


class CountingAccounts(FakeAccounts):
    def __init__(self) -> None:
        super().__init__()
        self.lookups = 0

    def find_login(self, email: str) -> tuple[Account, str] | None:
        self.lookups += 1
        return super().find_login(email)


def test_login_guessing_is_stopped_before_the_slow_password_check() -> None:
    accounts = CountingAccounts()
    with client(pipeline=object(), accounts=accounts, limits=limits()) as api:
        for _ in range(2):
            api.post("/auth/login", json={"email": "ana@example.com", "password": "guess one"})
        response = api.post("/auth/login", json={"email": "ANA@example.com", "password": "guess"})

    assert response.status_code == 429
    assert (
        response.json()["message"]
        == "Too many login attempts. Please wait a moment before trying again."
    )
    assert accounts.lookups == 2  # no account lookup, no Argon2 hash for the limited one


def test_signups_per_address() -> None:
    with client(pipeline=object(), accounts=FakeAccounts(), limits=limits()) as api:
        first = api.post("/auth/signup", json={"email": "a@example.com", "password": PASSWORD})
        second = api.post("/auth/signup", json={"email": "b@example.com", "password": PASSWORD})

    assert (first.status_code, second.status_code) == (201, 429)
