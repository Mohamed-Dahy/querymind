"""The endpoints for saved questions: list, save, remove.

Only a question's words are kept. Asking one again is a normal POST /ask/stream, with the same
checks and audit line as any other question.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, status

from querymind.api.conversations import Store
from querymind.api.schemas import ErrorResponse, SavedQuestionCreate, SavedQuestionList
from querymind.store.conversations import SavedQuestion

router = APIRouter(prefix="/saved-questions", tags=["saved questions"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse, "description": "An invalid id or body"},
    503: {"model": ErrorResponse, "description": "Saved questions aren't available"},
}


@router.get("", response_model=SavedQuestionList, responses=_ERRORS)
def list_saved_questions(store: Store) -> SavedQuestionList:
    """The saved questions, newest first."""
    return SavedQuestionList(questions=store.list_saved_questions())


@router.post("", response_model=SavedQuestion, responses=_ERRORS)
def save_question(saved: SavedQuestionCreate, store: Store) -> SavedQuestion:
    """Save a question. Saving the same words again gives back the row that is already there."""
    return store.add_saved_question(saved.question)


@router.delete("/{saved_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
def remove_saved_question(saved_id: UUID, store: Store) -> None:
    """Remove a saved question. Removing one that is already gone is fine."""
    store.remove_saved_question(saved_id)
