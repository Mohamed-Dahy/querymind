"""The web UI's test fixtures (web/src/mocks/) still match the API, so its tests can trust them.

The recorded replies were real once; these tests fail when a Pydantic model changes and the
fixtures would silently drift (re-record them with scripts/record_fixtures.py, or edit by hand).
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from querymind.api.main import _ERROR_MAP, _LLM_MESSAGES
from querymind.api.schemas import AskReply
from scripts.record_fixtures import DEFAULT_OUTPUT, RECORDINGS, Recording

MOCKS = Path(__file__).resolve().parents[2] / "web" / "src" / "mocks"

# Codes the API sends besides _ERROR_MAP's: a bad request body and the catch-all (api/main.py).
OTHER_ERRORS = {"invalid_request": 422, "internal_error": 500}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_recorder_writes_where_the_web_ui_reads() -> None:
    assert DEFAULT_OUTPUT == MOCKS / "recorded"


@pytest.mark.parametrize("recording", RECORDINGS, ids=lambda r: r.name)
def test_recorded_reply_matches_the_api_models_exactly(recording: Recording) -> None:
    data = load(DEFAULT_OUTPUT / f"{recording.name}.json")

    reply: BaseModel = TypeAdapter(AskReply).validate_python(data)

    assert reply.model_dump()["kind"] == recording.kind
    # Pydantic ignores unknown keys, so check both ways: no field missing, none left over.
    assert set(data) == set(type(reply).model_fields)


def test_there_is_an_error_fixture_for_every_code_with_its_real_status() -> None:
    errors = load(MOCKS / "errors.json")
    real = {code: status for status, code in _ERROR_MAP.values()} | OTHER_ERRORS

    assert {code: e["status"] for code, e in errors.items()} == real
    assert all(isinstance(e["message"], str) and e["message"] for e in errors.values())


def test_llm_error_fixtures_use_the_fixed_messages_the_api_sends() -> None:
    errors = load(MOCKS / "errors.json")
    codes = {error_type: code for error_type, (_, code) in _ERROR_MAP.items()}

    for error_type, message in _LLM_MESSAGES.items():
        assert errors[codes[error_type]]["message"] == message
