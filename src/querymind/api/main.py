"""The FastAPI app. Run it with:

    uv run uvicorn querymind.api.main:app --reload

Then open http://127.0.0.1:8000/docs for interactive API docs.

`create_app()` builds the app; tests call it with a fake pipeline. At startup the real app
loads settings (failing fast if .env is wrong), sets up JSON logging and creates one Pipeline
that every request shares.

Every error becomes the same small JSON shape, `{"error": code, "message": text}`, with a
fitting HTTP status. Stack traces and raw driver or LLM-vendor messages never reach the
client (§9). A rate limit also says how long to wait, when the provider told us: the field
`retry_after_seconds` and the standard `Retry-After` header.
"""

import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from querymind import __version__
from querymind.agent.pipeline import Pipeline
from querymind.api.deps import AnswersQuestions
from querymind.api.routes import router
from querymind.config import get_settings
from querymind.db.errors import DatabaseUnavailableError, QueryExecutionError, QueryTimeoutError
from querymind.llm import (
    LLMError,
    LLMOutputError,
    LLMProviderError,
    LLMRateLimitError,
    create_embedding_provider,
    create_llm_provider,
)
from querymind.logging_setup import configure_logging
from querymind.safety.errors import UnsafeSqlError

logger = logging.getLogger(__name__)

# Our exceptions → (HTTP status, stable error code). The message is the exception's own text,
# which our safety and database errors word for end users. Searched in order, so a subclass
# comes before its parent.
_ERROR_MAP: dict[type[Exception], tuple[int, str]] = {
    UnsafeSqlError: (422, "unsafe_sql"),
    QueryTimeoutError: (504, "query_timeout"),
    QueryExecutionError: (502, "query_failed"),
    DatabaseUnavailableError: (503, "db_unavailable"),
    LLMRateLimitError: (429, "llm_rate_limited"),
    LLMProviderError: (502, "llm_unavailable"),
    LLMOutputError: (502, "llm_bad_output"),
}

# LLM errors carry the vendor's raw reply (status codes, JSON bodies), so the client gets a
# fixed message instead and the details go to the server log only.
_LLM_MESSAGES: dict[type[Exception], str] = {
    LLMRateLimitError: "The AI provider's usage limit was reached. Please try again later.",
    LLMProviderError: "The AI provider did not respond (network or outage).",
    LLMOutputError: "The AI model declined or answered in the wrong format. Try rewording it.",
}


def create_app(pipeline: AnswersQuestions | None = None) -> FastAPI:
    """Build the app. Pass `pipeline` in tests; leave it out to build the real one at startup."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs once when the server starts (code before `yield`) and stops (after).
        if pipeline is not None:
            app.state.pipeline = pipeline
        else:
            configure_logging()
            settings = get_settings()  # raises ConfigError → the server refuses to start
            app.state.pipeline = Pipeline(
                llm=create_llm_provider(settings),
                settings=settings,
                embeddings=create_embedding_provider(settings),  # schema retrieval (3.3)
            )
            logger.info(
                "api started",
                extra={
                    "provider": settings.llm_provider,
                    "schema_retrieval": settings.schema_retrieval,
                },
            )
        yield

    app = FastAPI(
        title="QueryMind",
        description="Ask questions about your company database in plain language.",
        version=__version__,
        lifespan=lifespan,
    )
    app.include_router(router)
    for error_type in _ERROR_MAP:
        app.add_exception_handler(error_type, _handle_known_error)
    app.add_exception_handler(RequestValidationError, _handle_invalid_request)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    return app


def _error(
    status: int, code: str, message: str, *, retry_after_seconds: float | None = None
) -> JSONResponse:
    content: dict[str, str | int] = {"error": code, "message": message}
    headers: dict[str, str] = {}
    if retry_after_seconds is not None:
        # Whole seconds, as the header requires. Rounded up, so a retry is never too early.
        seconds = math.ceil(retry_after_seconds)
        content["retry_after_seconds"] = seconds
        headers["Retry-After"] = str(seconds)
    return JSONResponse(status_code=status, content=content, headers=headers)


async def _handle_known_error(request: Request, exc: Exception) -> JSONResponse:
    # Look up by class hierarchy, so a subclass of a mapped error is handled too.
    status, code = next(
        value for error_type, value in _ERROR_MAP.items() if isinstance(exc, error_type)
    )
    if isinstance(exc, UnsafeSqlError):
        message = exc.reason
    elif isinstance(exc, LLMError):
        logger.warning("llm error", extra={"error_code": code, "detail": str(exc)})
        message = next(text for kind, text in _LLM_MESSAGES.items() if isinstance(exc, kind))
    else:
        message = str(exc)
    wait = exc.retry_after_seconds if isinstance(exc, LLMRateLimitError) else None
    return _error(status, code, message, retry_after_seconds=wait)


async def _handle_invalid_request(request: Request, exc: Exception) -> JSONResponse:
    """Bad request body (e.g. empty question): same error shape as everything else."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
    return _error(422, "invalid_request", f"{field}: {first.get('msg', 'invalid request')}")


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug: log the full trace on the server, tell the client only that it failed."""
    logger.exception("unhandled error", extra={"path": request.url.path})
    return _error(500, "internal_error", "Something went wrong. Please try again.")


app = create_app()
