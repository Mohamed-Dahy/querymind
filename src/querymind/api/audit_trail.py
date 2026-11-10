"""Writing the audit log from the routes (5.5).

The routes know who is asking (the token) and how the request ended (a reply, an error, a
closed stream), so they write the line. The pipeline's own `querymind.audit` log line stays as it
is: evals and scripts, which have no user, keep using it.

A line that can't be written is logged as a warning and the request goes on: the answer is
already paid for. (The pipeline's log line is the second record of it.)
"""

import logging
import time
from uuid import UUID

from querymind.api.errors import error_response
from querymind.api.schemas import AskResponse, ClarificationResponse, RefusalResponse
from querymind.auth.tokens import AuthUser
from querymind.store.audit import Action, AuditEntry, AuditStore
from querymind.store.errors import StoreError

logger = logging.getLogger(__name__)


def write(audit: AuditStore | None, entry: AuditEntry) -> None:
    """Add the line; a failure is logged, never raised."""
    if audit is None:
        return
    try:
        audit.record(entry)
    except StoreError:
        logger.warning(
            "audit line not written",
            extra={"action": entry.action, "outcome": entry.outcome, "user_id": str(entry.user_id)},
        )


def elapsed_ms(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def for_reply(
    reply: AskResponse | ClarificationResponse | RefusalResponse,
    *,
    user: AuthUser,
    conversation_id: UUID,
    started: float,
    connection_id: UUID | None = None,
) -> AuditEntry:
    """A question that got a reply: its kind is the outcome; an answer adds its SQL and size."""
    answer = reply if isinstance(reply, AskResponse) else None
    return AuditEntry(
        action="ask",
        outcome=reply.kind,
        conversation_id=conversation_id,
        question=reply.question,
        sql=answer.sql if answer else None,
        tables=tuple(answer.tables_used) if answer else (),
        row_count=answer.row_count if answer else None,
        sql_attempts=answer.sql_attempts if answer else None,
        duration_ms=elapsed_ms(started),
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        email=user.email,
        connection_id=connection_id,
    )


def for_error(
    exc: Exception,
    *,
    action: Action,
    user: AuthUser,
    question: str,
    started: float,
    conversation_id: UUID | None = None,
    sql: str | None = None,
    connection_id: UUID | None = None,
) -> AuditEntry:
    """A request that failed: the API's error code is the outcome, its safe message the detail."""
    _, body = error_response(exc)
    return AuditEntry(
        action=action,
        outcome=body.error,
        detail=body.message,
        conversation_id=conversation_id,
        connection_id=connection_id,
        question=question,
        sql=sql,
        duration_ms=elapsed_ms(started),
        user_id=user.user_id,
        workspace_id=user.workspace_id,
        email=user.email,
    )
