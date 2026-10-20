"""Logging for the running app: one JSON object per line, including the structured fields.

All our code logs like `logger.info("ask", extra={"question": ..., "outcome": "ok"})`. Python's
default format only prints the message ("ask"), so the fields would be lost. This formatter
prints them too:

    {"time": "2026-10-20T14:51:57", "level": "INFO", "logger": "querymind.audit",
     "message": "ask", "question": "How many customers?", "outcome": "ok", ...}

One JSON object per line is also what log tools (CloudWatch in Phase 6) search best.
"""

import json
import logging
from datetime import UTC, datetime

# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


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
        # default=str: values JSON can't hold (dates, sets...) are written as text, never crash.
        return json.dumps(entry, default=str, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    """Send all `querymind` logs to the console as JSON lines. Call once at app start."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("querymind")
    logger.handlers = [handler]  # replace, so calling twice (e.g. --reload) doesn't duplicate
    logger.setLevel(level)
    logger.propagate = False
