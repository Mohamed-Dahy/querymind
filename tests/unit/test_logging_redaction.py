"""Secrets never reach the log (5.7): the RedactSecrets filter on the app's log handler.

These tests format through a real handler: pytest's `caplog` would see nothing, because the
`querymind` logger doesn't pass its records up once configure_logging has run (propagate=False).
"""

import io
import json
import logging
from collections.abc import Iterator

import pytest

from querymind.logging_setup import (
    REDACTED,
    JsonFormatter,
    RedactSecrets,
    configure_logging,
    redact,
)

GROQ_KEY = "gsk_live_0123456789abcdefghijklmnop"
GOOGLE_KEY = "AIzaSyD-0123456789abcdefghijklmnopq"
ANTHROPIC_KEY = "sk-ant-api03-0123456789abcdef"


@pytest.fixture
def written() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    """A logger whose lines go through the same formatter and filter as the app's."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactSecrets())
    logger = logging.getLogger("redaction-test")
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)  # else it inherits WARNING from the root logger
    yield logger, stream
    logger.removeHandler(handler)


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"Groq said: invalid API key {GROQ_KEY}", GROQ_KEY),
        (f"key={GOOGLE_KEY}", GOOGLE_KEY),
        (f"x-api-key: {ANTHROPIC_KEY}", ANTHROPIC_KEY),
        ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl", "eyJhbGci"),
        ("host=db.example.com password=hunter2-long user=ro", "hunter2-long"),
        # libpq quotes a value with spaces and escapes a quote inside it with a backslash.
        ("host=db password='it\\'s a secret' user=ro", "a secret"),
        ("postgresql://analyst:s3cr3t-pass@db.example.com:5432/sales", "s3cr3t-pass"),
        (f"GET https://api.example.com/v1/models?key={GOOGLE_KEY}&x=1", GOOGLE_KEY),
    ],
)
def test_secret_shapes_are_hidden(text: str, secret: str) -> None:
    hidden = redact(text)

    assert secret not in hidden
    assert REDACTED in hidden


def test_what_was_there_is_still_said() -> None:
    assert redact(f"key {GROQ_KEY}") == f"key gsk_{REDACTED}"
    assert redact("password=abc123 user=ro") == f"password={REDACTED} user=ro"


@pytest.mark.parametrize(
    "text",
    [
        "Which task-manager skills sold best?",  # "sk-" inside a word is not a key
        "SELECT * FROM customers WHERE city = 'Berlin'",
        "password reset requested",  # the word alone, no value
        "https://example.com/path?page=2",
    ],
)
def test_ordinary_text_is_left_alone(text: str) -> None:
    assert redact(text) == text


def test_a_key_in_the_message_or_its_arguments_is_hidden(
    written: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = written
    logger.warning("provider refused %s", GROQ_KEY)

    line = json.loads(stream.getvalue())
    assert GROQ_KEY not in stream.getvalue()
    assert line["message"] == f"provider refused gsk_{REDACTED}"


def test_a_key_in_extra_fields_is_hidden_even_nested(
    written: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = written
    logger.info(
        "llm call",
        extra={"detail": f"401: {GROQ_KEY}", "headers": {"auth": [f"Bearer {GOOGLE_KEY}"]}},
    )

    assert GROQ_KEY not in stream.getvalue()
    assert GOOGLE_KEY not in stream.getvalue()
    assert json.loads(stream.getvalue())["detail"] == f"401: gsk_{REDACTED}"


def test_a_key_in_a_traceback_is_hidden(written: tuple[logging.Logger, io.StringIO]) -> None:
    logger, stream = written
    try:
        raise RuntimeError(f"Invalid API key: {GROQ_KEY}")
    except RuntimeError:
        logger.exception("llm call failed")

    line = json.loads(stream.getvalue())
    assert GROQ_KEY not in stream.getvalue()
    assert "RuntimeError: Invalid API key: gsk_" in line["exception"]


@pytest.fixture
def app_logging() -> Iterator[None]:
    """configure_logging changes the global `querymind` logger: put it back afterwards."""
    logger = logging.getLogger("querymind")
    saved = (logger.handlers[:], logger.level, logger.propagate)
    yield
    logger.handlers, logger.level, logger.propagate = saved[0], saved[1], saved[2]


@pytest.mark.usefixtures("app_logging")
def test_the_apps_logging_hides_keys_from_every_module() -> None:
    configure_logging()
    [handler] = logging.getLogger("querymind").handlers
    assert isinstance(handler, logging.StreamHandler)
    stream = io.StringIO()
    handler.setStream(stream)

    # A child logger: a filter on the `querymind` logger itself would never see this record.
    logging.getLogger("querymind.llm.groq_provider").error("refused: %s", GROQ_KEY)

    assert GROQ_KEY not in stream.getvalue()
    assert f"gsk_{REDACTED}" in stream.getvalue()
