"""No route forgets the login (5.1c).

Walks every route in the API's OpenAPI schema and calls it with no token, and with a bad one.
Each must answer 401 `not_authenticated`, except the public ones listed here on purpose. A new
route without `CurrentUser` (or the explorer's router-level dependency) fails this test.
"""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from querymind.api.main import create_app
from tests.auth_helpers import make_tokens

# Public on purpose: health checks, the evals lab (our own eval data, portfolio material; decided
# 2026-11-08), and the login routes themselves (they read the refresh cookie, not a token).
PUBLIC = {
    ("GET", "/health"),
    ("GET", "/evals"),
    ("POST", "/auth/signup"),
    ("POST", "/auth/login"),
    ("POST", "/auth/refresh"),
    ("POST", "/auth/logout"),
}

APP = create_app(pipeline=object(), tokens=make_tokens())  # type: ignore[arg-type]
SPEC: dict[str, Any] = APP.openapi()
ROUTES = sorted((method.upper(), path) for path, item in SPEC["paths"].items() for method in item)
EXAMPLE_PARAMS = {
    "conversation_id": "13d134a4-4b0e-4b50-a799-42981a8c2bf2",
    "saved_id": "13d134a4-4b0e-4b50-a799-42981a8c2bf3",
    "n": "1",
    "table": "orders",
}


def filled(path: str) -> str:
    """The path with an example value for each {parameter}, so it reaches the route itself."""
    return re.sub(r"\{(\w+)\}", lambda m: EXAMPLE_PARAMS.get(m.group(1), "x"), path)


def test_the_public_list_is_exactly_what_answers_without_a_login() -> None:
    # Every public route really exists: a renamed one can't hide here as stale config.
    assert set(ROUTES) >= PUBLIC


@pytest.mark.parametrize(("method", "path"), [r for r in ROUTES if r not in PUBLIC])
@pytest.mark.parametrize("token", [None, "not-a-real-token"], ids=["no token", "bad token"])
def test_every_other_route_needs_a_login(method: str, path: str, token: str | None) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with TestClient(APP, raise_server_exceptions=False) as client:
        response = client.request(method, filled(path), headers=headers, json={})

    assert response.status_code == 401, (method, path, response.text)
    assert response.json()["error"] == "not_authenticated"
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_the_schema_says_how_to_log_in() -> None:
    # Swagger's "Authorize" button and the generated web types both read this.
    schemes = SPEC["components"]["securitySchemes"]
    assert any(scheme.get("scheme") == "bearer" for scheme in schemes.values())
