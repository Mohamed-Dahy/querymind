"""Which HTTP status, code and message each of our exceptions becomes.

Shared by the error handlers of `POST /ask` (main.py) and the `error` event of `POST /ask/stream`
(routes.py), so a failure reads the same on both. Stack traces and raw driver or LLM-vendor
messages never reach the client (§9).
"""

import logging
import math

from querymind.api.evals import EvalsUnavailableError
from querymind.api.ratelimit import RateLimitedError
from querymind.api.schemas import ErrorResponse
from querymind.auth.errors import (
    EmailTakenError,
    ForbiddenError,
    InvalidLoginError,
    NotAuthenticatedError,
)
from querymind.db.errors import (
    DatabaseUnavailableError,
    QueryExecutionError,
    QueryTimeoutError,
    RemoteLoginError,
    RemoteTlsError,
    RemoteUnreachableError,
    UnsafeConnectionError,
    WritableLoginError,
)
from querymind.llm import (
    LLMAuthError,
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
)
from querymind.llm.key_check import KeyCheckError
from querymind.safety.errors import UnsafeSqlError
from querymind.secrets.errors import SecretUnreadableError
from querymind.store.errors import (
    AccountsUnavailableError,
    ConnectionNameTakenError,
    ConnectionNotFoundError,
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
    LLMAuthError: (502, "llm_key_rejected"),  # before its parent, below
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
    ForbiddenError: (403, "forbidden"),
    RateLimitedError: (429, "rate_limited"),
    KeyCheckError: (422, "llm_key_invalid"),  # saving a key (5.8): our own wording
    SecretUnreadableError: (409, "llm_key_unreadable"),  # wrong SECRETS_KEY: save it again
    # A user's own database (5.9). Each message is our own wording: what to fix.
    UnsafeConnectionError: (422, "connection_unsafe"),
    RemoteUnreachableError: (422, "connection_unreachable"),
    RemoteLoginError: (422, "connection_login_failed"),
    RemoteTlsError: (422, "connection_tls_failed"),
    WritableLoginError: (422, "connection_writable"),
    ConnectionNotFoundError: (404, "connection_not_found"),
    ConnectionNameTakenError: (409, "connection_name_taken"),
}

# LLM errors carry the vendor's raw reply (status codes, JSON bodies), so the client gets a
# fixed message instead and the details go to the server log only.
LLM_MESSAGES: dict[type[Exception], str] = {
    LLMRateLimitError: "The AI provider's usage limit was reached. Please try again later.",
    LLMAuthError: (
        "The AI provider refused the API key. If your workspace saved its own key, an admin "
        "can update it in Settings."
    ),
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
    limited = (LLMRateLimitError, RateLimitedError)  # ours (5.6) or the LLM provider's
    wait = exc.retry_after_seconds if isinstance(exc, limited) else None
    # Whole seconds, as the Retry-After header requires. Rounded up, so a retry is never too early.
    seconds = math.ceil(wait) if wait is not None else None
    return status, ErrorResponse(error=code, message=message, retry_after_seconds=seconds)
