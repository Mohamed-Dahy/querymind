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
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from querymind import __version__
from querymind.agent.pipeline import Pipeline
from querymind.api.admin import router as admin_router
from querymind.api.auth import CookieSettings
from querymind.api.auth import router as auth_router
from querymind.api.conversations import router as conversations_router
from querymind.api.deps import AnswersQuestions, ManagesConversations, StreamsAnswers
from querymind.api.errors import ERROR_MAP, INTERNAL_ERROR, error_response
from querymind.api.evals import EvalsData, create_evals
from querymind.api.evals import router as evals_router
from querymind.api.explorer import ExplorerData, create_explorer
from querymind.api.explorer import router as explorer_router
from querymind.api.routes import router
from querymind.api.saved_questions import router as saved_questions_router
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import TokenService
from querymind.config import get_settings
from querymind.llm import create_embedding_provider, create_llm_provider
from querymind.logging_setup import configure_logging
from querymind.store.accounts import AccountStore
from querymind.store.audit import AuditStore
from querymind.store.conversations import ConversationStore, create_store, load_history

logger = logging.getLogger(__name__)


def create_app(
    pipeline: AnswersQuestions | StreamsAnswers | ManagesConversations | None = None,
    store: ConversationStore | None = None,
    explorer: ExplorerData | None = None,
    evals: EvalsData | None = None,
    accounts: AccountStore | None = None,
    tokens: TokenService | None = None,
    auth_cookie: CookieSettings | None = None,
    audit: AuditStore | None = None,
) -> FastAPI:
    """Build the app. Pass `pipeline` (and the others) in tests; leave `pipeline` out to build the
    real ones at startup, which then need logins set up (APP_DB_USER and JWT_SECRET, 5.1)."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Runs once when the server starts (code before `yield`) and stops (after).
        if pipeline is not None:
            app.state.pipeline = pipeline
            app.state.store = store
            app.state.explorer = explorer
            app.state.evals = evals
            app.state.accounts = accounts
            app.state.tokens = tokens
            app.state.auth_cookie = auth_cookie
            app.state.audit = audit
        else:
            configure_logging()
            settings = get_settings()  # raises ConfigError → the server refuses to start
            secret = settings.require_logins()  # no logins = no API (ConfigError)
            app.state.accounts = AccountStore(settings)
            app.state.tokens = TokenService(secret, access_minutes=settings.access_token_minutes)
            app.state.auth_cookie = CookieSettings.from_settings(settings)
            app.state.audit = AuditStore(settings)  # needs the app's role, as logins do
            app.state.store = create_store(settings)
            app.state.explorer = create_explorer(settings)
            app.state.evals = create_evals(settings)
            app.state.pipeline = Pipeline(
                llm=create_llm_provider(settings),
                settings=settings,
                embeddings=create_embedding_provider(settings),  # schema retrieval (3.3)
                history_loader=(
                    partial(load_history, app.state.store) if app.state.store else None
                ),
            )
            logger.info(
                "api started",
                extra={
                    "provider": settings.llm_provider,
                    "schema_retrieval": settings.schema_retrieval,
                    "history": settings.history_enabled,
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
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(conversations_router)
    app.include_router(saved_questions_router)
    app.include_router(explorer_router)
    app.include_router(evals_router)
    for error_type in ERROR_MAP:
        app.add_exception_handler(error_type, _handle_known_error)
    app.add_exception_handler(RequestValidationError, _handle_invalid_request)
    app.add_exception_handler(Exception, _handle_unexpected_error)
    return app


def _error(status: int, body: ErrorResponse) -> JSONResponse:
    """The body as JSON, with only the fields it has. A wait also goes in the standard header, and
    a 401 says how to log in (RFC 6750: the Bearer scheme)."""
    wait = body.retry_after_seconds
    headers = {"Retry-After": str(wait)} if wait is not None else {}
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status, content=body.model_dump(exclude_none=True), headers=headers or None
    )


async def _handle_known_error(request: Request, exc: Exception) -> JSONResponse:
    return _error(*error_response(exc))


async def _handle_invalid_request(request: Request, exc: Exception) -> JSONResponse:
    """Bad request body (e.g. empty question): same error shape as everything else."""
    errors = exc.errors() if isinstance(exc, RequestValidationError) else []
    first = errors[0] if errors else {}
    field = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
    message = f"{field}: {first.get('msg', 'invalid request')}"
    return _error(422, ErrorResponse(error="invalid_request", message=message))


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """A bug: log the full trace on the server, tell the client only that it failed."""
    logger.exception("unhandled error", extra={"path": request.url.path})
    return _error(500, INTERNAL_ERROR)


app = create_app()
