"""Logging for the running app: one JSON object per line, including the structured fields.

All our code logs like `logger.info("ask", extra={"question": ..., "outcome": "ok"})`. Python's
default format only prints the message ("ask"), so the fields would be lost. This formatter
prints them too:

    {"time": "2026-10-20T14:51:57", "level": "INFO", "logger": "querymind.audit",
     "message": "ask", "question": "How many customers?", "outcome": "ok", ...}

One JSON object per line is also what log tools (CloudWatch in Phase 6) search best.

Secrets (5.7): every line also passes `RedactSecrets`, a last line of defence that hides anything
shaped like an API key, a bearer token or a password in a connection string, wherever it is in the
record (message, extra fields, traceback). The first line of defence is that secrets stay
`SecretStr` (repr `**********`) and are never logged on purpose. The filter recognises *shapes*:
a database password on its own looks like any other word, so it can't be caught here.
"""

import json
import logging
import re
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


REDACTED = "[redacted]"

# Each pattern's group "s" is the secret part: only that is replaced, so the line still says
# what was there ("gsk_[redacted]", "password=[redacted]").
_SECRET_PATTERNS = (
    # LLM API keys by their known prefixes: Groq gsk_, OpenAI / Anthropic sk-, Google AIza.
    re.compile(r"\b(?:gsk_|sk-|AIza)(?P<s>[A-Za-z0-9_\-]{8,})"),
    # Authorization headers (our JWTs, providers' keys).
    re.compile(r"(?i)\bbearer\s+(?P<s>[A-Za-z0-9._~+/\-]+=*)"),
    # Connection strings: password=... (libpq) and scheme://user:password@host (URLs).
    re.compile(r"(?i)\bpassword\s*=\s*(?P<s>'(?:[^'\\]|\\.)*'|[^\s,;]+)"),
    re.compile(r"\b[a-z][a-z0-9+.\-]*://[^:/\s@]+:(?P<s>[^@\s]+)@"),
    # Keys in a URL's query string (?key=AIza...).
    re.compile(r"(?i)[?&](?:key|api_key|apikey|token)=(?P<s>[^&\s]+)"),
)


def _hide(match: re.Match[str]) -> str:
    start, end = match.span("s")
    whole = match.group(0)
    offset = match.start()
    return whole[: start - offset] + REDACTED + whole[end - offset :]


def redact(text: str) -> str:
    """`text` with every secret-shaped part replaced by [redacted]."""
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_hide, text)
    return text


def _redact_value(value: object) -> object:
    """Redact a field from `extra=`: strings, and strings inside lists and dicts."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _redact_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_redact_value(item) for item in value]
    # Anything else would be written with str() (JsonFormatter's default=str): redact that text.
    return redact(str(value))


class RedactSecrets(logging.Filter):
    """Hide secret-shaped text in a log record before it is written. Put it on the handler, so it
    sees records from every logger below `querymind` (a filter on a logger sees only its own)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None  # already merged into msg
        for key in [key for key in vars(record) if key not in _STANDARD_ATTRIBUTES]:
            setattr(record, key, _redact_value(getattr(record, key)))
        if record.exc_info:
            # The traceback text, redacted, replaces the exception: its message may quote a key.
            record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = redact(record.stack_info)
        return True


class JsonFormatter(logging.Formatter):
    """Format a record as one JSON line: time, level, logger, message, then the extra fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update(
            (key, value) for key, value in vars(record).items() if key not in _STANDARD_ATTRIBUTES
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        elif record.exc_text:  # already formatted (and redacted) by RedactSecrets
            entry["exception"] = record.exc_text
        # default=str: values JSON can't hold (dates, sets...) are written as text, never crash.
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Send all `querymind` logs to the console as JSON lines. Call once at app start."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactSecrets())
    logger = logging.getLogger("querymind")
    logger.handlers = [handler]  # replace, so calling twice (e.g. --reload) doesn't duplicate
    logger.setLevel(level)
    logger.propagate = False
