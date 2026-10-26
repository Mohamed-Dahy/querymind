"""The OpenAPI export the web UI's types are generated from (scripts/export_openapi.py)."""

import json
from pathlib import Path

from scripts.export_openapi import openapi_schema, render


def test_schema_has_the_endpoints_and_models_the_web_ui_uses() -> None:
    schema = openapi_schema()

    assert set(schema["paths"]) >= {"/ask", "/health"}
    models = schema["components"]["schemas"]
    for name in ("AskRequest", "AskResponse", "ClarificationResponse", "RefusalResponse"):
        assert name in models
    # Errors are documented, so the UI's types know their shape too.
    assert "ErrorResponse" in models
    assert "ErrorResponse" in json.dumps(schema["paths"]["/ask"]["post"]["responses"]["502"])


def test_ask_reply_is_a_union_told_apart_by_kind() -> None:
    reply = openapi_schema()["paths"]["/ask"]["post"]["responses"]["200"]
    body = reply["content"]["application/json"]["schema"]

    assert body["discriminator"]["propertyName"] == "kind"
    assert set(body["discriminator"]["mapping"]) == {"answer", "clarification", "refusal"}


def test_render_is_stable_and_ends_with_a_newline() -> None:
    first = render(openapi_schema())

    assert first == render(openapi_schema())
    assert first.endswith("}\n")
    assert "\r" not in first


def test_render_sorts_keys_and_keeps_unicode_readable() -> None:
    text = render({"b": "—", "a": 1})

    assert text == '{\n  "a": 1,\n  "b": "—"\n}\n'


def test_committed_copy_is_up_to_date() -> None:
    # The same check CI runs (U0.6): after a Pydantic change, run scripts/export_openapi.py.
    committed = Path(__file__).resolve().parents[2] / "web" / "openapi.json"

    assert committed.read_text(encoding="utf-8") == render(openapi_schema())
