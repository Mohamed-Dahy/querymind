"""The web UI's test fixtures (web/src/mocks/) still match the API, so its tests can trust them.

The recorded replies were real once; these tests fail when a Pydantic model changes and the
fixtures would silently drift (re-record them with scripts/record_fixtures.py, or edit by hand).
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, TypeAdapter

from querymind.api.errors import ERROR_MAP, LLM_MESSAGES
from querymind.api.schemas import AskReply, StreamEvent
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


@pytest.mark.parametrize("recording", RECORDINGS, ids=lambda r: r.name)
def test_recorded_stream_holds_valid_events_and_nothing_private(recording: Recording) -> None:
    path = DEFAULT_OUTPUT / "streams" / f"{recording.name}.json"
    events = load(path)

    parsed = [TypeAdapter(StreamEvent).validate_python(event) for event in events]

    assert parsed[0].model_dump()["type"] == "step"
    # Only the steps before the end: the reply itself is the <name>.json file.
    assert not {"final", "error"} & {event["type"] for event in events}
    text = path.read_text(encoding="utf-8")
    assert '"detail"' not in text and '"reason"' not in text and '"schema_text"' not in text


def test_the_repair_recording_really_repaired() -> None:
    reply = load(DEFAULT_OUTPUT / "repair.json")
    events = load(DEFAULT_OUTPUT / "streams" / "repair.json")

    assert [attempt["kind"] for attempt in reply["attempts"]] == ["db_error"]
    assert reply["sql_attempts"] == 2
    assert "repair_sql" in {event.get("node") for event in events}


def test_there_is_an_error_fixture_for_every_code_with_its_real_status() -> None:
    errors = load(MOCKS / "errors.json")
    real = {code: status for status, code in ERROR_MAP.values()} | OTHER_ERRORS

    assert {code: e["status"] for code, e in errors.items()} == real
    assert all(isinstance(e["message"], str) and e["message"] for e in errors.values())


def test_llm_error_fixtures_use_the_fixed_messages_the_api_sends() -> None:
    errors = load(MOCKS / "errors.json")
    codes = {error_type: code for error_type, (_, code) in ERROR_MAP.items()}

    for error_type, message in LLM_MESSAGES.items():
        assert errors[codes[error_type]]["message"] == message
