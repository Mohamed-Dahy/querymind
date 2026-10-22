"""The HTTP endpoints: POST /ask and GET /health."""

from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends

from querymind import __version__
from querymind.api.deps import AnswersQuestions, get_pipeline
from querymind.api.schemas import (
    AskReply,
    AskRequest,
    AskResponse,
    ClarificationResponse,
    ErrorResponse,
    HealthResponse,
    RefusalResponse,
    reply_from,
)

router = APIRouter()

# Documented in OpenAPI so API users know every error shape in advance (handlers in main.py).
_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid question, or unsafe generated SQL"},
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


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness check: the server is up. It doesn't touch the DB or LLM (see DEV_GUIDE)."""
    return HealthResponse(status="ok", version=__version__)
