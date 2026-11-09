"""The HTTP endpoints: POST /ask, POST /ask/stream and GET /health."""

import logging
import time
from collections.abc import Generator, Sequence
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse

from querymind import __version__
from querymind.agent.events import AgentEvent, Finished
from querymind.api import audit_trail
from querymind.api.deps import (
    AnswersQuestions,
    CurrentUser,
    StreamsAnswers,
    get_audit,
    get_pipeline,
    get_store,
    get_streaming_pipeline,
    get_workspace_llms,
    limit_questions,
    owner_of,
)
from querymind.api.errors import error_response
from querymind.api.schemas import (
    AskReply,
    AskRequest,
    AskResponse,
    ClarificationResponse,
    ErrorEvent,
    ErrorResponse,
    FinalEvent,
    HealthResponse,
    RefusalResponse,
    StreamEvent,
    reply_from,
)
from querymind.auth.tokens import AuthUser
from querymind.llm import LLMProvider
from querymind.llm.workspace import WorkspaceLLMs
from querymind.store.audit import AuditEntry, AuditStore
from querymind.store.conversations import ConversationStore, Owner
from querymind.store.errors import StoreError

logger = logging.getLogger(__name__)

router = APIRouter()

type OpenRuns = list[Generator[AgentEvent | Finished]]


def close_runs_when_the_response_ends() -> Generator[OpenRuns]:
    """Closes the route's pipeline run when the response ends, however it ended.

    A client going away only cancels the response: FastAPI doesn't close a sync generator, so the
    run would wait, half-read, holding its conversation's lock, until Python collects it (165 s
    in a real test). FastAPI runs this dependency's clean-up as soon as the response has ended, and
    after the step working now, so the run isn't still executing.
    """
    runs: OpenRuns = []
    yield runs
    for run in runs:
        run.close()


def _check_owner(
    store: ConversationStore | None, conversation_id: UUID | None, owner: Owner
) -> None:
    """A follow-up only continues your own conversation: someone else's id is a 404, before the
    agent loads any of its turns. (A new id, or no store, is fine.)"""
    if store is not None and conversation_id is not None:
        store.check_owner(conversation_id, owner=owner)


def _workspace_llm(llms: WorkspaceLLMs | None, user: AuthUser) -> LLMProvider | None:
    """The LLM for this user's question: their workspace's own key (5.8), or None for the demo
    key in .env. Called inside the route's `try`, so a key that can't be read still gets the
    question's audit line (§8.7), like every other failure."""
    return llms.provider_for(user.workspace_id) if llms is not None else None


def _save(
    store: ConversationStore | None,
    conversation_id: UUID,
    owner: Owner,
    reply: AskResponse | ClarificationResponse | RefusalResponse,
    trace: Sequence[AgentEvent] = (),
) -> None:
    """Save a finished turn. A failure is logged and goes no further: the answer is already paid
    for, so it must still reach the user, even though a reload won't find it."""
    if store is None:
        return
    try:
        store.save_turn(
            conversation_id,
            owner=owner,
            question=reply.question,
            kind=reply.kind,
            sql=reply.sql if isinstance(reply, AskResponse) else None,
            reply=reply.model_dump(mode="json"),
            trace=[event.model_dump(mode="json") for event in trace],
        )
    except StoreError:
        logger.warning("turn not saved", extra={"conversation_id": str(conversation_id)})


# Documented in OpenAPI so API users know every error shape in advance (handlers in main.py).
_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    404: {"model": ErrorResponse, "description": "The conversation isn't yours, or doesn't exist"},
    422: {"model": ErrorResponse, "description": "Invalid question, or unsafe generated SQL"},
    429: {"model": ErrorResponse, "description": "Too many questions, or the LLM's usage limit"},
    502: {"model": ErrorResponse, "description": "The LLM or the database failed"},
    503: {"model": ErrorResponse, "description": "The database is not reachable"},
    504: {"model": ErrorResponse, "description": "The query took too long"},
}


@router.post(
    "/ask",
    response_model=AskReply,
    responses=_ERRORS,
    tags=["questions"],
    dependencies=[Depends(limit_questions)],  # 5.6: 429 when over a limit
)
def ask(
    request: AskRequest,
    user: CurrentUser,
    pipeline: Annotated[AnswersQuestions, Depends(get_pipeline)],
    store: Annotated[ConversationStore | None, Depends(get_store)],
    audit: Annotated[AuditStore | None, Depends(get_audit)],
    llms: Annotated[WorkspaceLLMs | None, Depends(get_workspace_llms)],
) -> AskResponse | ClarificationResponse | RefusalResponse:
    """Answer a question about the data: the answer, the SQL that ran, and the rows.

    Or, with `kind` = "clarification", a question back with options (the question was
    ambiguous), or with `kind` = "refusal", why QueryMind won't do it (e.g. change data).

    Every reply has a `conversation_id`: send it with the next question to follow up on this
    one ("now only for 1997"). Without one, a new conversation starts.
    """
    # A plain `def` (not `async def`): the pipeline blocks on the database and the LLM, so
    # FastAPI runs it in a worker thread and the server stays free for other requests.
    started = time.perf_counter()
    owner = owner_of(user)
    conversation_id = request.conversation_id or uuid4()
    try:
        _check_owner(store, request.conversation_id, owner)
        llm = _workspace_llm(llms, user)  # 5.8: the workspace's own key, or None (demo)
        reply = pipeline.ask(request.question, conversation_id=str(conversation_id), llm=llm)
    except Exception as exc:
        # Every question gets its audit line (5.5), however it ended. Then the usual error reply.
        line = audit_trail.for_error(
            exc,
            action="ask",
            user=user,
            question=request.question,
            started=started,
            conversation_id=conversation_id,
        )
        audit_trail.write(audit, line)
        raise
    response = reply_from(reply, conversation_id=conversation_id)
    _save(store, conversation_id, owner, response)
    line = audit_trail.for_reply(
        response, user=user, conversation_id=conversation_id, started=started
    )
    audit_trail.write(audit, line)
    return response


@router.post(
    "/ask/stream",
    response_class=EventSourceResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Not logged in"},
        422: {"model": ErrorResponse, "description": "Invalid question"},
        429: {
            "model": ErrorResponse,
            "description": "Too many questions: wait Retry-After seconds",
        },
    },
    tags=["questions"],
    # Checked before the stream starts, so an over-limit answer is a plain HTTP 429, not an event.
    dependencies=[Depends(limit_questions)],
)
def ask_stream(
    request: AskRequest,
    user: CurrentUser,
    pipeline: Annotated[StreamsAnswers, Depends(get_streaming_pipeline)],
    runs: Annotated[OpenRuns, Depends(close_runs_when_the_response_ends)],
    store: Annotated[ConversationStore | None, Depends(get_store)],
    audit: Annotated[AuditStore | None, Depends(get_audit)],
    llms: Annotated[WorkspaceLLMs | None, Depends(get_workspace_llms)],
) -> Generator[StreamEvent]:
    """The same as `POST /ask`, reporting each step as it happens (Server-Sent Events).

    Each event is one JSON object with a `type`. The last is `final` (the `POST /ask` reply) or
    `error` (its error body): the status is already sent, so a failed run still answers 200.

    Closing the connection stops the run after the step working now, and nothing is saved (the
    audit log says `cancelled`).
    """
    # A plain `def`, as in `ask` above: FastAPI runs each step of this generator in a worker thread.
    started = time.perf_counter()
    owner = owner_of(user)
    conversation_id = request.conversation_id or uuid4()
    written = False  # the audit line, once per question (5.5)
    try:
        _check_owner(store, request.conversation_id, owner)  # a 404 arrives as the error event
        llm = _workspace_llm(llms, user)  # an unreadable key, too
        run = pipeline.stream(request.question, conversation_id=str(conversation_id), llm=llm)
        runs.append(run)
        trace: list[AgentEvent] = []
        for event in run:
            if isinstance(event, Finished):
                response = reply_from(event.reply, conversation_id=conversation_id)
                # Saved before the answer goes out, so a reload right after it finds the turn.
                _save(store, conversation_id, owner, response, trace)
                line = audit_trail.for_reply(
                    response, user=user, conversation_id=conversation_id, started=started
                )
                audit_trail.write(audit, line)
                written = True
                yield FinalEvent(reply=response)
            else:
                trace.append(event)
                yield event
    except Exception as exc:
        line = audit_trail.for_error(
            exc,
            action="ask",
            user=user,
            question=request.question,
            started=started,
            conversation_id=conversation_id,
        )
        audit_trail.write(audit, line)
        written = True
        _, body = error_response(exc)
        yield ErrorEvent(error=body)
    finally:
        # The client went away mid-run (Stop, a closed tab): the generator is closed here.
        if not written:
            audit_trail.write(
                audit,
                AuditEntry(
                    action="ask",
                    outcome="cancelled",
                    user_id=user.user_id,
                    workspace_id=user.workspace_id,
                    email=user.email,
                    conversation_id=conversation_id,
                    question=request.question,
                    duration_ms=audit_trail.elapsed_ms(started),
                ),
            )


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness check: the server is up. It doesn't touch the DB or LLM (see DEV_GUIDE)."""
    return HealthResponse(status="ok", version=__version__)
