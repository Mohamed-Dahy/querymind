"""Row-level security (5.3): which rows a member of the workspace may see.

    GET /admin/members/{user_id}/row-scope   the member's role and scope (admins)
    PUT /admin/members/{user_id}/row-scope   {"row_scope": ["Eastern"]}, or null = every row

A scope is a list of tags. The database's own policies decide what they mean: on the demo, the
Northwind sales regions (Eastern, Western, Northern, Southern) of the employee who took an order;
on a workspace's own database, whatever its policies read from `querymind.scope`. So tags aren't
checked against one database: a tag no policy knows simply shows no row (fail closed).

Admins always see every row, so an admin's scope can't be set. Members are added, re-roled and
removed with api/members.py (5.12); `GET /admin/members` lists them with their scope.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from querymind.api import audit_trail
from querymind.api.deps import AdminUser, get_audit
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import Role
from querymind.safety.row_scope import MAX_TAGS, RowScopeInvalidError, checked
from querymind.store.audit import AuditEntry, AuditStore
from querymind.store.errors import MemberNotFoundError, StoreUnavailableError
from querymind.store.row_scope import RowScopeStore

router = APIRouter(prefix="/admin/members", tags=["admin"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    404: {"model": ErrorResponse, "description": "Not a member of this workspace"},
    503: {"model": ErrorResponse, "description": "Row permissions aren't available"},
}


class RowScopeInfo(BaseModel):
    """A member's role and the rows they may see."""

    user_id: UUID
    role: Role
    row_scope: list[str] | None = Field(
        description="The tags of the rows they see; null = every row"
    )


class RowScopeUpdate(BaseModel):
    """A member's new scope: a list of tags, or null for every row."""

    model_config = ConfigDict(extra="forbid")

    row_scope: list[str] | None = Field(default=None, max_length=MAX_TAGS)


def get_row_scope_store(request: Request) -> RowScopeStore:
    """The scopes store; a server without it can't manage row permissions (503)."""
    store: RowScopeStore | None = getattr(request.app.state, "row_scopes", None)
    if store is None:
        raise StoreUnavailableError("Row permissions aren't available on this server.")
    return store


Store = Annotated[RowScopeStore, Depends(get_row_scope_store)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]


def _member(store: RowScopeStore, admin: AdminUser, user_id: UUID) -> RowScopeInfo:
    found = store.member(admin.workspace_id, user_id)
    if found is None:
        raise MemberNotFoundError("This workspace has no such member.")
    role, scope = found
    return RowScopeInfo(user_id=user_id, role=role, row_scope=None if scope is None else [*scope])


@router.get("/{user_id}/row-scope", response_model=RowScopeInfo, responses=_ERRORS)
def get_row_scope(user_id: UUID, admin: AdminUser, store: Store) -> RowScopeInfo:
    """A member's role and the rows they may see. Admins only."""
    return _member(store, admin, user_id)


@router.put(
    "/{user_id}/row-scope",
    response_model=RowScopeInfo,
    responses={
        **_ERRORS,
        422: {"model": ErrorResponse, "description": "A scope that can't be set"},
    },
)
def put_row_scope(
    user_id: UUID, update: RowScopeUpdate, admin: AdminUser, store: Store, audit: Audit
) -> RowScopeInfo:
    """Set which rows a member may see (null = every row). Admins only; audited."""
    member = _member(store, admin, user_id)
    if member.role == "admin":
        raise RowScopeInvalidError("Admins always see every row: their scope can't be set.")
    scope = None if update.row_scope is None else checked(update.row_scope)
    if not store.set(admin.workspace_id, user_id, scope):
        raise MemberNotFoundError("This workspace has no such member.")  # removed meanwhile
    audit_trail.write(
        audit,
        AuditEntry(
            action="row_scope_changed",
            outcome="ok",
            user_id=admin.user_id,
            workspace_id=admin.workspace_id,
            email=admin.email,
            detail=f"member {user_id}: " + ("every row" if scope is None else ", ".join(scope)),
        ),
    )
    return member.model_copy(update={"row_scope": None if scope is None else [*scope]})
