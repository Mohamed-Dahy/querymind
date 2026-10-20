"""The HTTP endpoints: POST /ask and GET /health."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from querymind import __version__
from querymind.api.deps import AnswersQuestions, get_pipeline
from querymind.api.schemas import AskRequest, AskResponse, ErrorResponse, HealthResponse

router = APIRouter()

# Documented in OpenAPI so API users know every error shape in advance (handlers in main.py).
_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "Invalid question, or unsafe generated SQL"},
    502: {"model": ErrorResponse, "description": "The LLM or the database failed"},
    504: {"model": ErrorResponse, "description": "The query took too long"},
}


@router.post("/ask", response_model=AskResponse, responses=_ERRORS, tags=["questions"])
def ask(
    request: AskRequest, pipeline: Annotated[AnswersQuestions, Depends(get_pipeline)]
) -> AskResponse:
    """Answer a question about the data: the answer, the SQL that ran, and the rows."""
    # A plain `def` (not `async def`): the pipeline blocks on the database and the LLM, so
    # FastAPI runs it in a worker thread and the server stays free for other requests.
    return AskResponse.from_result(pipeline.ask(request.question))


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness check: the server is up. It doesn't touch the DB or LLM (see DEV_GUIDE)."""
    return HealthResponse(status="ok", version=__version__)
