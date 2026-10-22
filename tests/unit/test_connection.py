"""connect_readonly when the database can't be reached: a clean error, details only in the log."""

import logging
from typing import Any

import psycopg
import pytest
from pydantic import SecretStr

from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError

RAW = 'connection to server at "127.0.0.1", port 5433 failed: Connection refused'


def settings() -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "groq_api_key": SecretStr("gsk_test"),
    }
    return Settings.model_validate(values)


def test_unreachable_database_raises_a_clean_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Stand-in for a stopped database. A real refused port is slow on Windows (~10 s timeout).
    def refuse(conninfo: str) -> psycopg.Connection:
        raise psycopg.OperationalError(RAW)

    monkeypatch.setattr(psycopg, "connect", refuse)
    with caplog.at_level(logging.WARNING), pytest.raises(DatabaseUnavailableError) as info:
        connect_readonly(settings())
    assert str(info.value) == "Could not connect to the database. Please try again later."
    assert "5433" not in str(info.value)  # host/port stay out of the user's message...
    assert isinstance(info.value.__cause__, psycopg.OperationalError)  # ...kept for debugging
    assert any(getattr(r, "detail", None) == RAW for r in caplog.records)  # ...and logged


def test_other_errors_are_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    # Only "couldn't connect" is translated; a bug (e.g. a bad conninfo string) stays visible.
    def broken(conninfo: str) -> psycopg.Connection:
        raise psycopg.ProgrammingError("invalid connection option")

    monkeypatch.setattr(psycopg, "connect", broken)
    with pytest.raises(psycopg.ProgrammingError):
        connect_readonly(settings())
