"""Workspace members (5.12): an admin adds signed-up users, changes their role, removes them.

    GET    /admin/members              every member: email, role, rows (5.3), when they joined
    POST   /admin/members              {"email": "ana@example.com", "role": "viewer"} -> 201
    PATCH  /admin/members/{user_id}    {"role": "analyst"}
    DELETE /admin/members/{user_id}    -> 204

Admins only, in the admin's current workspace; every change is audited (member_added,
role_changed, member_removed). A change applies at the member's next request: the API reads the
role from the membership every time (api/deps.py `get_current_user`).

Adding by email tells the admin whether that email has an account (404 if not). That is the price
of "no invitations" (decided with Mohamed): the route is rate-limited per admin, and failed tries
are audited too, so someone probing a list of emails shows up in the log.

A workspace always keeps an admin: demoting or removing the last one is refused (409).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from querymind.api import audit_trail
from querymind.api.deps import AdminUser, get_audit, get_members, limit_member_adds
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import AuthUser, Role
from querymind.store.audit import Action, AuditEntry, AuditStore
from querymind.store.errors import AccountNotFoundError, AlreadyMemberError
from querymind.store.members import Member, MemberStore

router = APIRouter(prefix="/admin/members", tags=["admin"])
Members = Annotated[MemberStore, Depends(get_members)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    503: {"model": ErrorResponse, "description": "Members can't be managed right now"},
}
_NOT_A_MEMBER: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not a member of this workspace"},
    409: {"model": ErrorResponse, "description": "The workspace's last admin"},
}


class MemberInfo(BaseModel):
    """A member of the workspace."""

    user_id: UUID
    email: str
    role: Role
    row_scope: list[str] | None = Field(
        description="The tags of the rows they see (5.3); null = every row"
    )
    added_at: str = Field(description="When they joined the workspace (ISO 8601)")


class MemberList(BaseModel):
    members: list[MemberInfo]


class MemberAdd(BaseModel):
    """Who to add: someone who has signed up already, and their role here."""

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Role


class RoleUpdate(BaseModel):
    """A member's new role."""

    model_config = ConfigDict(extra="forbid")

    role: Role


def _info(member: Member) -> MemberInfo:
    return MemberInfo(
        user_id=member.user_id,
        email=member.email,
        role=member.role,
        row_scope=None if member.row_scope is None else [*member.row_scope],
        added_at=member.added_at.isoformat(),
    )


def _audit(audit: Audit, admin: AuthUser, action: Action, outcome: str, detail: str) -> None:
    """One audit line by the admin who did it, in their workspace. The member is in `detail`."""
    audit_trail.write(
        audit,
        AuditEntry(
            action=action,
            outcome=outcome,
            user_id=admin.user_id,
            workspace_id=admin.workspace_id,
            email=admin.email,
            detail=detail,
        ),
    )


@router.get("", response_model=MemberList, responses=_ERRORS)
def list_members(admin: AdminUser, members: Members) -> MemberList:
    """Every member of your workspace, with their role and rows. Admins only."""
    return MemberList(members=[_info(member) for member in members.list(admin.workspace_id)])


@router.post(
    "",
    response_model=MemberInfo,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_member_adds)],  # checks the admin role first
    responses={
        **_ERRORS,
        404: {"model": ErrorResponse, "description": "No account has this email"},
        409: {"model": ErrorResponse, "description": "Already a member"},
        429: {"model": ErrorResponse, "description": "Too many tries"},
    },
)
def add_member(body: MemberAdd, admin: AdminUser, members: Members, audit: Audit) -> MemberInfo:
    """Add someone who has signed up to your workspace, with a role. Admins only; audited."""
    email = body.email.lower()  # accounts are stored lower-case (sign-up)
    try:
        member = members.add(admin.workspace_id, email, body.role)
    except (AccountNotFoundError, AlreadyMemberError) as exc:
        outcome = "account_not_found" if isinstance(exc, AccountNotFoundError) else "already_member"
        _audit(audit, admin, "member_added", outcome, f"{email} as {body.role}")
        raise
    _audit(audit, admin, "member_added", "ok", f"{email} as {body.role}")
    return _info(member)


@router.patch("/{user_id}", response_model=MemberInfo, responses={**_ERRORS, **_NOT_A_MEMBER})
def change_role(
    user_id: UUID, body: RoleUpdate, admin: AdminUser, members: Members, audit: Audit
) -> MemberInfo:
    """Give a member another role (applies at their next request). Admins only; audited."""
    member, before = members.set_role(admin.workspace_id, user_id, body.role)
    if before != member.role:  # the same role again changes nothing: no line
        _audit(audit, admin, "role_changed", "ok", f"{member.email}: {before} -> {member.role}")
    return _info(member)


@router.delete(
    "/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**_ERRORS, **_NOT_A_MEMBER},
)
def remove_member(user_id: UUID, admin: AdminUser, members: Members, audit: Audit) -> None:
    """Take a member out of your workspace (yourself too, if another admin stays). Audited."""
    member = members.remove(admin.workspace_id, user_id)
    _audit(audit, admin, "member_removed", "ok", f"{member.email} ({member.role})")
