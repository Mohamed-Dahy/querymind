"""The store without a database: titles, and a database that can't be reached."""

from unittest.mock import MagicMock
from uuid import uuid4

import psycopg
import pytest

from querymind.db.errors import DatabaseUnavailableError
from querymind.store.conversations import ConversationStore, like_pattern, title_from
from querymind.store.errors import StoreUnavailableError
from tests.auth_helpers import TEST_OWNER


@pytest.mark.parametrize(
    ("question", "title"),
    [
        ("Top customers?", "Top customers?"),
        ("  spaced out  ", "spaced out"),
        ("First line\nsecond line", "First line"),
        ("   ", "Untitled"),
    ],
)
def test_title_from(question: str, title: str) -> None:
    assert title_from(question) == title


def test_a_long_title_is_cut_at_a_word() -> None:
    title = title_from("word " * 40)

    assert title.endswith("word…")
    assert len(title) <= 81


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("sales", "%sales%"),
        ("100%", "%100\\%%"),
        ("a_b", "%a\\_b%"),
        ("c:\\x", "%c:\\\\x%"),
    ],
)
def test_like_pattern_escapes_wildcards(text: str, pattern: str) -> None:
    assert like_pattern(text) == pattern


@pytest.mark.parametrize(
    "failure",
    [DatabaseUnavailableError("down"), psycopg.OperationalError("host, port and user in here")],
)
def test_a_failing_database_is_one_clean_error(failure: Exception) -> None:
    store = ConversationStore(MagicMock(), connect=MagicMock(side_effect=failure))

    with pytest.raises(StoreUnavailableError) as info:
        store.get(uuid4(), owner=TEST_OWNER)

    assert "host" not in str(info.value)
    assert info.value.__cause__ is failure
