"""The endpoints for saved conversations: list, read, rename or pin, delete, and re-run.

No route here takes SQL from the client. A re-run takes only which turn, and runs what is stored
through the validator and executor again, as the agent's read-only role, with no LLM call.
"""

import time
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import TypeAdapter

from querymind.api import audit_trail
from querymind.api.deps import (
    CurrentUser,
    ManagesConversations,
    get_audit,
    get_conversation_pipeline,
    get_store,
    limit_reruns,
    owner_of,
)
from querymind.api.schemas import (
    AskReply,
    ConversationDetail,
    ConversationList,
    ConversationUpdate,
    ErrorResponse,
    RerunResponse,
    SavedTurnResponse,
)
from querymind.store.audit import AuditEntry, AuditStore
from querymind.store.conversations import ConversationStore, ConversationSummary, SavedTurn
from querymind.store.errors import StoreUnavailableError, TurnNotFoundError

router = APIRouter(prefix="/conversations", tags=["conversations"])

_REPLY = TypeAdapter[Any](AskReply)

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    404: {"model": ErrorResponse, "description": "No such conversation or turn (of yours)"},
    422: {"model": ErrorResponse, "description": "An invalid id or body"},
    503: {"model": ErrorResponse, "description": "Saved conversations aren't available"},
}


def require_store(
    store: Annotated[ConversationStore | None, Depends(get_store)],
) -> ConversationStore:
    """The store, or a clear error when history is off."""
    if store is None:
        raise StoreUnavailableError("Saved conversations aren't set up on this server.")
    return store


Store = Annotated[ConversationStore, Depends(require_store)]


def _saved_turn(turn: SavedTurn) -> SavedTurnResponse:
    """A saved turn in the API's reply shape. An answer's rows weren't stored: `rows` is empty and
    `row_count` still says how many there were."""
    reply = {**turn.reply, "rows": []} if turn.kind == "answer" else turn.reply
    return SavedTurnResponse(
        n=turn.n,
        created_at=turn.created_at,
        reply=_REPLY.validate_python(reply),
        trace=turn.trace,  # Pydantic validates these into events
    )


@router.get("", response_model=ConversationList, responses=_NOT_FOUND)
def list_conversations(
    user: CurrentUser,
    store: Store,
    q: Annotated[str | None, Query(max_length=200, description="Find in titles, questions")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> ConversationList:
    """Your saved conversations: pinned first, then the most recent."""
    found, has_more = store.list_conversations(
        owner=owner_of(user), search=q or None, limit=limit, offset=offset
    )
    return ConversationList(conversations=found, has_more=has_more)


@router.get("/{conversation_id}", response_model=ConversationDetail, responses=_NOT_FOUND)
def get_conversation(conversation_id: UUID, user: CurrentUser, store: Store) -> ConversationDetail:
    """One conversation, with its turns. Answers come without rows: re-run one for fresh rows."""
    saved = store.get(conversation_id, owner=owner_of(user))
    return ConversationDetail(
        **saved.model_dump(exclude={"saved_turns"}),
        saved_turns=[_saved_turn(turn) for turn in saved.saved_turns],
    )


@router.patch("/{conversation_id}", response_model=ConversationSummary, responses=_NOT_FOUND)
def update_conversation(
    conversation_id: UUID, change: ConversationUpdate, user: CurrentUser, store: Store
) -> ConversationSummary:
    """Rename or pin a conversation."""
    return store.update(
        conversation_id, owner=owner_of(user), title=change.title, pinned=change.pinned
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_NOT_FOUND)
def delete_conversation(
    conversation_id: UUID,
    user: CurrentUser,
    store: Store,
    pipeline: Annotated[ManagesConversations, Depends(get_conversation_pipeline)],
) -> None:
    """Delete a conversation for good, from the store and from the agent's memory."""
    store.delete(conversation_id, owner=owner_of(user))  # 404 unless it's yours
    pipeline.forget(str(conversation_id))


@router.post(
    "/{conversation_id}/turns/{n}/rerun",
    response_model=RerunResponse,
    responses=_NOT_FOUND,
    dependencies=[Depends(limit_reruns)],  # 5.6
)
def rerun_turn(
    conversation_id: UUID,
    n: Annotated[int, Path(ge=1)],
    user: CurrentUser,
    store: Store,
    pipeline: Annotated[ManagesConversations, Depends(get_conversation_pipeline)],
    audit: Annotated[AuditStore | None, Depends(get_audit)],
) -> RerunResponse:
    """Run a saved answer's SQL again for fresh rows. No LLM call; there is no request body."""
    started = time.perf_counter()
    question, sql = "", None
    try:
        turn = store.get_turn(conversation_id, n, owner=owner_of(user))
        question, sql = turn.question, turn.sql
        if sql is None:
            raise TurnNotFoundError("This turn has no SQL to run.")
        result = pipeline.rerun(question, sql, conversation_id=str(conversation_id))
    except Exception as exc:
        line = audit_trail.for_error(
            exc,
            action="rerun",
            user=user,
            question=question,
            started=started,
            conversation_id=conversation_id,
            sql=sql,
        )
        audit_trail.write(audit, line)
        raise
    audit_trail.write(
        audit,
        AuditEntry(
            action="rerun",
            outcome="answer",
            user_id=user.user_id,
            workspace_id=user.workspace_id,
            email=user.email,
            conversation_id=conversation_id,
            question=question,
            sql=sql,
            row_count=result.row_count,
            duration_ms=audit_trail.elapsed_ms(started),
        ),
    )
    return RerunResponse(
        columns=result.columns,
        rows=result.rows,
        row_count=result.row_count,
        truncated=result.truncated,
        ran_at=datetime.now(UTC),
    )
