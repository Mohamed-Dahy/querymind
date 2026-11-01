"""The HTTP endpoints: POST /ask, POST /ask/stream and GET /health."""

from collections.abc import Generator
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse

from querymind import __version__
from querymind.agent.events import AgentEvent, Finished
from querymind.api.deps import (
    AnswersQuestions,
    StreamsAnswers,
    get_pipeline,
    get_streaming_pipeline,
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


# Documented in OpenAPI so API users know every error shape in advance (handlers in main.py).
_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid question, or unsafe generated SQL"},
    429: {"model": ErrorResponse, "description": "The LLM provider's usage limit was reached"},
    502: {"model": ErrorResponse, "description": "The LLM or the database failed"},
    503: {"model": ErrorResponse, "description": "The database is not reachable"},
    504: {"model": ErrorResponse, "description": "The query took too long"},
}


@router.post("/ask", response_model=AskReply, responses=_ERRORS, tags=["questions"])
def ask(
    request: AskRequest, pipeline: Annotated[AnswersQuestions, Depends(get_pipeline)]
) -> AskResponse | ClarificationResponse | RefusalResponse:
    """Answer a question about the data: the answer, the SQL that ran, and the rows.

    Or, with `kind` = "clarification", a question back with options (the question was
    ambiguous), or with `kind` = "refusal", why QueryMind won't do it (e.g. change data).

    Every reply has a `conversation_id`: send it with the next question to follow up on this
    one ("now only for 1997"). Without one, a new conversation starts.
    """
    # A plain `def` (not `async def`): the pipeline blocks on the database and the LLM, so
    # FastAPI runs it in a worker thread and the server stays free for other requests.
    conversation_id = request.conversation_id or uuid4()
    reply = pipeline.ask(request.question, conversation_id=str(conversation_id))
    return reply_from(reply, conversation_id=conversation_id)


@router.post(
    "/ask/stream",
    response_class=EventSourceResponse,
    responses={422: {"model": ErrorResponse, "description": "Invalid question"}},
    tags=["questions"],
)
def ask_stream(
    request: AskRequest,
    pipeline: Annotated[StreamsAnswers, Depends(get_streaming_pipeline)],
    runs: Annotated[OpenRuns, Depends(close_runs_when_the_response_ends)],
) -> Generator[StreamEvent]:
    """The same as `POST /ask`, reporting each step as it happens (Server-Sent Events).

    Each event is one JSON object with a `type`. The last is `final` (the `POST /ask` reply) or
    `error` (its error body): the status is already sent, so a failed run still answers 200.

    Closing the connection stops the run after the step working now.
    """
    # A plain `def`, as in `ask` above: FastAPI runs each step of this generator in a worker thread.
    conversation_id = request.conversation_id or uuid4()
    try:
        run = pipeline.stream(request.question, conversation_id=str(conversation_id))
        runs.append(run)
        for event in run:
            if isinstance(event, Finished):
                yield FinalEvent(reply=reply_from(event.reply, conversation_id=conversation_id))
            else:
                yield event
    except Exception as exc:
        _, body = error_response(exc)
        yield ErrorEvent(error=body)


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness check: the server is up. It doesn't touch the DB or LLM (see DEV_GUIDE)."""
    return HealthResponse(status="ok", version=__version__)
