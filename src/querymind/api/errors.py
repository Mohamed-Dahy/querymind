"""Which HTTP status, code and message each of our exceptions becomes.

Shared by the error handlers of `POST /ask` (main.py) and the `error` event of `POST /ask/stream`
(routes.py), so a failure reads the same on both. Stack traces and raw driver or LLM-vendor
messages never reach the client (§9).
"""

import logging
import math

from querymind.api.evals import EvalsUnavailableError
from querymind.api.schemas import ErrorResponse
from querymind.auth.errors import EmailTakenError, InvalidLoginError, NotAuthenticatedError
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.llm import LLMError, LLMOutputError, LLMProviderError, LLMRateLimitError
from querymind.safety.errors import UnsafeSqlError
from querymind.store.errors import (
    AccountsUnavailableError,
    ConversationNotFoundError,
    StoreUnavailableError,
    TurnNotFoundError,
)

logger = logging.getLogger(__name__)

# Our exceptions → (HTTP status, stable error code). The message is the exception's own text,
# which our safety and database errors word for end users. Searched in order, so a subclass
# comes before its parent.
ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    UnsafeSqlError: (422, "unsafe_sql"),
    QueryTimeoutError: (504, "query_timeout"),
    QueryExecutionError: (502, "query_failed"),
    DatabaseUnavailableError: (503, "db_unavailable"),
    LLMRateLimitError: (429, "llm_rate_limited"),
    LLMProviderError: (502, "llm_unavailable"),
    LLMOutputError: (502, "llm_bad_output"),
    ConversationNotFoundError: (404, "conversation_not_found"),
    TurnNotFoundError: (404, "turn_not_found"),
    AccountsUnavailableError: (503, "accounts_unavailable"),  # before its parent, below
    StoreUnavailableError: (503, "history_unavailable"),
    EvalsUnavailableError: (503, "evals_unavailable"),
    NotAuthenticatedError: (401, "not_authenticated"),
    InvalidLoginError: (401, "invalid_login"),
    EmailTakenError: (409, "email_taken"),
}

# LLM errors carry the vendor's raw reply (status codes, JSON bodies), so the client gets a
# fixed message instead and the details go to the server log only.
LLM_MESSAGES: dict[type[Exception], str] = {
    LLMRateLimitError: "The AI provider's usage limit was reached. Please try again later.",
    LLMProviderError: "The AI provider did not respond (network or outage).",
    LLMOutputError: "The AI model declined or answered in the wrong format. Try rewording it.",
}

INTERNAL_ERROR = ErrorResponse(
    error="internal_error", message="Something went wrong. Please try again."
)


def error_response(exc: Exception) -> tuple[int, ErrorResponse]:
    """The status and body for any exception. One we don't know is a bug: logged, never shown."""
    mapped = next((v for error_type, v in ERROR_MAP.items() if isinstance(exc, error_type)), None)
    if mapped is None:
        logger.error("unhandled error", exc_info=exc)
        return 500, INTERNAL_ERROR
    status, code = mapped
    if isinstance(exc, UnsafeSqlError):
        message = exc.reason
    elif isinstance(exc, LLMError):
        logger.warning("llm error", extra={"error_code": code, "detail": str(exc)})
        message = next(text for kind, text in LLM_MESSAGES.items() if isinstance(exc, kind))
    else:
        message = str(exc)
    wait = exc.retry_after_seconds if isinstance(exc, LLMRateLimitError) else None
    # Whole seconds, as the Retry-After header requires. Rounded up, so a retry is never too early.
    seconds = math.ceil(wait) if wait is not None else None
    return status, ErrorResponse(error=code, message=message, retry_after_seconds=seconds)
