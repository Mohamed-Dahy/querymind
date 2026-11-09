"""The audit log for workspace admins (5.5): `GET /admin/audit`.

Admins only (`require_admin`: the role in their token), and only their own workspace's lines. A
failed login with an email nobody has belongs to no workspace, so it shows in no one's list: it is
in the database for whoever runs the server.
"""

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from querymind.api.deps import AdminUser, get_audit
from querymind.api.schemas import ErrorResponse
from querymind.store.audit import Action, AuditRecord, AuditStore
from querymind.store.errors import StoreUnavailableError

router = APIRouter(prefix="/admin", tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    503: {"model": ErrorResponse, "description": "The audit log isn't available"},
}


class AuditLine(BaseModel):
    """One line of the audit log, as the API shows it."""

    id: int
    at: datetime
    action: Action
    outcome: str = Field(
        description="answer, clarification, refusal, ok, cancelled, or an error code"
    )
    email: str | None
    conversation_id: UUID | None
    question: str | None
    sql: str | None
    tables: list[str]
    row_count: int | None
    duration_ms: int | None
    sql_attempts: int | None
    detail: str | None = Field(description="An error's message")

    @classmethod
    def of(cls, record: AuditRecord) -> "AuditLine":
        return cls.model_validate(
            {
                **record.model_dump(exclude={"user_id", "workspace_id"}),
                "tables": list(record.tables),
            }
        )


class AuditPage(BaseModel):
    lines: list[AuditLine]
    has_more: bool = Field(description="Ask again with before=<the last line's id> for more")


@router.get("/audit", response_model=AuditPage, responses=_ERRORS)
def audit_log(
    admin: AdminUser,
    audit: Annotated[AuditStore | None, Depends(get_audit)],
    action: Action | None = None,
    outcome: Annotated[str | None, Query(max_length=50)] = None,
    email: Annotated[str | None, Query(max_length=254)] = None,
    q: Annotated[str | None, Query(max_length=200, description="Find in questions and SQL")] = None,
    before: Annotated[int | None, Query(ge=1, description="Lines older than this id")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    """Your workspace's audit log, newest first: who asked what, the SQL, and how it ended."""
    if audit is None:
        raise StoreUnavailableError("The audit log isn't set up on this server.")
    lines, has_more = audit.list(
        admin.workspace_id,
        action=action,
        outcome=outcome,
        email=email.lower() if email else None,
        search=q or None,
        before=before,
        limit=limit,
    )
    return AuditPage(lines=[AuditLine.of(line) for line in lines], has_more=has_more)
