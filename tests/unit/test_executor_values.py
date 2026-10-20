"""to_json_value turns database values into JSON-safe ones; execute_query refuses plain strings."""

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any, cast

import psycopg
import pytest

from querymind.db.executor import execute_query, to_json_value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, True),
        (42, 42),
        (2.5, 2.5),
        ("Berlin", "Berlin"),
        (Decimal("1234.56"), 1234.56),
        (dt.date(1997, 1, 31), "1997-01-31"),
        (dt.datetime(1997, 1, 31, 12, 30), "1997-01-31T12:30:00"),
        (dt.time(8, 15), "08:15:00"),
        (dt.timedelta(days=3), "3 days, 0:00:00"),
        (uuid.UUID("12345678-1234-5678-1234-567812345678"), "12345678-1234-5678-1234-567812345678"),
        (b"\x89PNG....", "<binary, 8 bytes>"),  # bytes are never passed on
        (memoryview(b"abc"), "<binary, 3 bytes>"),
        ([1, Decimal("2.5"), None], [1, 2.5, None]),  # arrays: converted item by item
        ({"a": dt.date(2020, 1, 1)}, {"a": "2020-01-01"}),  # json columns
    ],
)
def test_converts_to_json_safe_values(value: object, expected: object) -> None:
    converted = to_json_value(value)
    assert converted == expected
    json.dumps(converted)  # really JSON-serialisable


def test_unknown_types_become_text() -> None:
    class Money:
        def __str__(self) -> str:
            return "$5.00"

    assert to_json_value(Money()) == "$5.00"


def test_plain_sql_strings_are_refused() -> None:
    # The executor must never run SQL that skipped the validator. mypy catches this too;
    # the cast hides it from mypy so we can prove the runtime check works.
    conn = cast(psycopg.Connection, object())
    with pytest.raises(TypeError, match="needs a ValidatedQuery"):
        execute_query(conn, cast(Any, "DELETE FROM orders"), timeout_seconds=1)
