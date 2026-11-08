"""Access tokens (5.1b): every way a token can be wrong is a NotAuthenticatedError."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from querymind.auth.errors import NotAuthenticatedError
from querymind.auth.tokens import (
    ALGORITHM,
    ISSUER,
    TokenService,
    hash_refresh_token,
    new_refresh_token,
)
from tests.auth_helpers import TEST_SECRET, TEST_USER

TOKENS = TokenService(TEST_SECRET, access_minutes=15)


def claims(**changes: object) -> dict[str, object]:
    now = datetime.now(UTC)
    base: dict[str, object] = {
        "iss": ISSUER,
        "sub": str(TEST_USER.user_id),
        "wid": str(TEST_USER.workspace_id),
        "role": TEST_USER.role,
        "email": TEST_USER.email,
        "typ": "access",
        "iat": now,
        "exp": now + timedelta(minutes=15),
    }
    base.update(changes)
    return {key: value for key, value in base.items() if value is not None}


def signed(payload: dict[str, object], secret: str = TEST_SECRET.get_secret_value()) -> str:
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def test_a_token_says_who_it_is_for() -> None:
    token, seconds = TOKENS.issue_access(TEST_USER)

    assert TOKENS.read_access(token) == TEST_USER
    assert seconds == 15 * 60


def test_it_expires() -> None:
    token, _ = TOKENS.issue_access(TEST_USER, now=datetime.now(UTC) - timedelta(minutes=16))

    with pytest.raises(NotAuthenticatedError):
        TOKENS.read_access(token)


def test_a_changed_token_is_refused() -> None:
    token, _ = TOKENS.issue_access(TEST_USER)
    header, _payload, signature = token.split(".")
    # Another user's id in the payload: the signature no longer matches.
    other = jwt.encode(claims(sub="33333333-3333-4333-8333-333333333333"), "x" * 40, ALGORITHM)
    forged = f"{header}.{other.split('.')[1]}.{signature}"

    with pytest.raises(NotAuthenticatedError):
        TOKENS.read_access(forged)


@pytest.mark.parametrize(
    "token",
    [
        signed(claims(), secret="another-secret-just-as-long-as-the-real-one-0123"),
        signed(claims(iss="someone-else")),
        signed(claims(typ="refresh")),  # only access tokens open the API
        signed(claims(typ=None)),
        signed(claims(exp=None)),  # a token that never expires isn't accepted
        signed(claims(role="owner")),  # not a role we have
        signed(claims(sub="not-a-uuid")),
        signed(claims(wid=None)),
        # The "none" algorithm: no signature at all. The algorithm list refuses it.
        jwt.encode(claims(), key="", algorithm="none"),
        "not.a.token",
        "",
    ],
    ids=[
        "wrong secret",
        "wrong issuer",
        "refresh type",
        "no type",
        "no expiry",
        "unknown role",
        "bad user id",
        "no workspace",
        "alg none",
        "garbage",
        "empty",
    ],
)
def test_every_bad_token_is_refused(token: str) -> None:
    with pytest.raises(NotAuthenticatedError):
        TOKENS.read_access(token)


def test_a_service_with_another_secret_refuses_our_tokens() -> None:
    token, _ = TOKENS.issue_access(TEST_USER)
    other = TokenService(SecretStr("a-completely-different-secret-of-enough-length-42"))

    with pytest.raises(NotAuthenticatedError):
        other.read_access(token)


def test_refresh_tokens_are_random_and_only_their_hash_is_kept() -> None:
    first, second = new_refresh_token(), new_refresh_token()

    assert first != second and len(first) >= 43  # 32 random bytes, base64
    assert hash_refresh_token(first) == hash_refresh_token(first)
    assert first not in hash_refresh_token(first)
