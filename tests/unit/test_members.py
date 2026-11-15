"""Workspace members over HTTP (5.12): adding, re-roling and removing, and the role read fresh.

The store is a fake here (its locking and the database's answers: tests/integration/
test_members_db.py). What is checked: admins only, the error codes, the audit lines, the rate
limit, and that the API takes the role from the membership on every request, not from the token.
"""

import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.main import create_app
from querymind.api.ratelimit import RateLimits
from querymind.auth.tokens import AuthUser, Role
from querymind.store.audit import AuditEntry
from querymind.store.errors import (
    AccountNotFoundError,
    AlreadyMemberError,
    LastAdminError,
    MemberNotFoundError,
    StoreUnavailableError,
)
from querymind.store.members import Member
from tests.auth_helpers import TEST_USER, KnownAccounts, make_tokens, signed_in

VIEWER = TEST_USER.model_copy(update={"role": "viewer"})
ANA = UUID("55555555-5555-4555-8555-555555555555")
JOINED = datetime(2026, 11, 15, 9, 30, tzinfo=UTC)


class FakeMembers:
    """One workspace's members in a dict, with the store's rules (signed up first, one admin)."""

    def __init__(self) -> None:
        self.accounts = {"ana@example.com": ANA}  # who has signed up
        self.members: dict[UUID, Member] = {
            TEST_USER.user_id: Member(
                user_id=TEST_USER.user_id,
                email=TEST_USER.email,
                role="admin",
                row_scope=None,
                added_at=JOINED,
            )
        }
        self.error: Exception | None = None

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    def list(self, workspace_id: UUID) -> list[Member]:
        self._check()
        return list(self.members.values())

    def add(self, workspace_id: UUID, email: str, role: Role) -> Member:
        self._check()
        user_id = self.accounts.get(email)
        if user_id is None:
            raise AccountNotFoundError("No account has this email: they need to sign up first.")
        if user_id in self.members:
            raise AlreadyMemberError("They are already a member of this workspace.")
        member = Member(user_id=user_id, email=email, role=role, row_scope=None, added_at=JOINED)
        self.members[user_id] = member
        return member

    def _guard(self, user_id: UUID, keeps_admin: bool) -> Member:
        member = self.members.get(user_id)
        if member is None:
            raise MemberNotFoundError("This workspace has no such member.")
        admins = {m.user_id for m in self.members.values() if m.role == "admin"}
        if member.role == "admin" and not keeps_admin and not admins - {user_id}:
            raise LastAdminError("A workspace needs an admin: make someone else an admin first.")
        return member

    def set_role(self, workspace_id: UUID, user_id: UUID, role: Role) -> tuple[Member, Role]:
        before = self._guard(user_id, keeps_admin=role == "admin")
        scope = None if role == "admin" else before.row_scope
        after = before.model_copy(update={"role": role, "row_scope": scope})
        self.members[user_id] = after
        return after, before.role

    def remove(self, workspace_id: UUID, user_id: UUID) -> Member:
        member = self._guard(user_id, keeps_admin=False)
        del self.members[user_id]
        return member


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def record(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


def client(
    members: FakeMembers,
    user: AuthUser = TEST_USER,
    audit: FakeAudit | None = None,
    limits: RateLimits | None = None,
) -> TestClient:
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        members=members,  # type: ignore[arg-type]
        audit=audit,  # type: ignore[arg-type]
        limits=limits,
    )
    return TestClient(signed_in(app, user), raise_server_exceptions=False)


def test_an_admin_lists_the_members_with_role_and_rows() -> None:
    members = FakeMembers()
    members.members[ANA] = Member(
        user_id=ANA, email="ana@example.com", role="viewer", row_scope=("Eastern",), added_at=JOINED
    )
    with client(members) as api:
        response = api.get("/admin/members")

    assert response.status_code == 200
    rows = response.json()["members"]
    assert [(m["email"], m["role"], m["row_scope"]) for m in rows] == [
        ("tester@example.com", "admin", None),
        ("ana@example.com", "viewer", ["Eastern"]),
    ]
    assert rows[0]["added_at"] == "2026-11-15T09:30:00+00:00"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/admin/members", None),
        ("POST", "/admin/members", {"email": "ana@example.com", "role": "viewer"}),
        ("PATCH", f"/admin/members/{ANA}", {"role": "admin"}),
        ("DELETE", f"/admin/members/{ANA}", None),
    ],
)
def test_only_an_admin_manages_members(method: str, path: str, body: Any) -> None:
    members = FakeMembers()
    with client(members, user=VIEWER) as api:
        response = api.request(method, path, json=body)

    assert response.status_code == 403
    assert response.json()["error"] == "forbidden"
    assert ANA not in members.members  # nothing was added


def test_adding_a_signed_up_user_with_a_role_is_audited() -> None:
    members, audit = FakeMembers(), FakeAudit()
    with client(members, audit=audit) as api:
        response = api.post("/admin/members", json={"email": "Ana@Example.com", "role": "viewer"})

    assert response.status_code == 201
    assert response.json()["email"] == "ana@example.com"  # matched as sign-up stored it
    assert response.json()["role"] == "viewer" and response.json()["row_scope"] is None
    (line,) = audit.entries
    assert (line.action, line.outcome, line.detail) == (
        "member_added",
        "ok",
        "ana@example.com as viewer",
    )
    assert line.user_id == TEST_USER.user_id  # the admin who did it


@pytest.mark.parametrize(
    ("email", "status", "error"),
    [
        ("nobody@example.com", 404, "account_not_found"),
        ("tester@example.com", 409, "already_member"),
    ],
)
def test_an_unknown_email_or_a_member_is_refused_and_audited(
    email: str, status: int, error: str
) -> None:
    members, audit = FakeMembers(), FakeAudit()
    members.accounts["tester@example.com"] = TEST_USER.user_id
    with client(members, audit=audit) as api:
        response = api.post("/admin/members", json={"email": email, "role": "analyst"})

    assert (response.status_code, response.json()["error"]) == (status, error)
    # Failed tries are in the log too: someone probing emails shows up there.
    assert [(e.action, e.outcome) for e in audit.entries] == [("member_added", error)]


@pytest.mark.parametrize(
    "body",
    [
        {"email": "ana@example.com", "role": "owner"},
        {"email": "not an email", "role": "viewer"},
        {"email": "ana@example.com", "role": "viewer", "row_scope": ["Eastern"]},
    ],
)
def test_a_bad_add_request_is_a_422(body: dict[str, Any]) -> None:
    with client(FakeMembers()) as api:
        response = api.post("/admin/members", json=body)

    assert response.status_code == 422


def test_adding_is_rate_limited_per_admin() -> None:
    limits = RateLimits(
        questions_per_minute=10,
        questions_per_day=10,
        questions_total_per_day=10,
        reruns_per_minute=10,
        logins_per_ip_per_minute=10,
        logins_per_email_per_minute=10,
        signups_per_ip_per_hour=10,
        member_adds_per_hour=2,
        clock=time.monotonic,
    )
    with client(FakeMembers(), limits=limits) as api:
        statuses = [
            api.post(
                "/admin/members", json={"email": f"x{n}@example.com", "role": "viewer"}
            ).status_code
            for n in range(3)
        ]

    assert statuses == [404, 404, 429]


def test_a_role_change_is_audited_and_the_same_role_again_is_not() -> None:
    members, audit = FakeMembers(), FakeAudit()
    members.add(TEST_USER.workspace_id, "ana@example.com", "viewer")
    with client(members, audit=audit) as api:
        changed = api.patch(f"/admin/members/{ANA}", json={"role": "analyst"})
        again = api.patch(f"/admin/members/{ANA}", json={"role": "analyst"})

    assert changed.status_code == again.status_code == 200
    assert changed.json()["role"] == "analyst"
    assert [(e.action, e.detail) for e in audit.entries] == [
        ("role_changed", "ana@example.com: viewer -> analyst")
    ]


@pytest.mark.parametrize("change", ["demote", "remove"])
def test_the_last_admin_stays(change: str) -> None:
    members = FakeMembers()
    me = TEST_USER.user_id
    with client(members) as api:
        if change == "demote":
            response = api.patch(f"/admin/members/{me}", json={"role": "viewer"})
        else:
            response = api.delete(f"/admin/members/{me}")

    assert response.status_code == 409
    assert response.json()["error"] == "last_admin"
    assert members.members[me].role == "admin"


def test_with_another_admin_you_can_step_down() -> None:
    members = FakeMembers()
    members.add(TEST_USER.workspace_id, "ana@example.com", "admin")
    with client(members) as api:
        response = api.patch(f"/admin/members/{TEST_USER.user_id}", json={"role": "analyst"})

    assert response.status_code == 200 and response.json()["role"] == "analyst"


def test_removing_a_member_is_audited() -> None:
    members, audit = FakeMembers(), FakeAudit()
    members.add(TEST_USER.workspace_id, "ana@example.com", "viewer")
    with client(members, audit=audit) as api:
        response = api.delete(f"/admin/members/{ANA}")
        gone = api.delete(f"/admin/members/{ANA}")

    assert response.status_code == 204 and ANA not in members.members
    assert (gone.status_code, gone.json()["error"]) == (404, "member_not_found")
    assert [(e.action, e.detail) for e in audit.entries] == [
        ("member_removed", "ana@example.com (viewer)")
    ]


def test_a_store_failure_is_a_503() -> None:
    members = FakeMembers()
    members.error = StoreUnavailableError("Members can't be read or changed right now.")
    with client(members) as api:
        response = api.get("/admin/members")

    assert response.status_code == 503


# --- The role comes from the membership, on every request (get_current_user) ---

TOKENS = make_tokens()


def real_token_client(accounts: KnownAccounts | None) -> TestClient:
    """No `signed_in` shortcut: a real token, checked and then looked up like on the server."""
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=accounts,  # type: ignore[arg-type]
        tokens=TOKENS,
        members=FakeMembers(),  # type: ignore[arg-type]
    )
    return TestClient(app, raise_server_exceptions=False)


def bearer(user: AuthUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS.issue_access(user)[0]}"}


def test_a_demoted_admin_loses_admin_routes_at_their_next_request() -> None:
    accounts = KnownAccounts(TEST_USER)
    headers = bearer(TEST_USER)  # issued while they were an admin
    with real_token_client(accounts) as api:
        before = api.get("/admin/members", headers=headers)
        accounts.members[(TEST_USER.user_id, TEST_USER.workspace_id)] = VIEWER  # demoted
        after = api.get("/admin/members", headers=headers)

    assert before.status_code == 200
    assert (after.status_code, after.json()["error"]) == (403, "forbidden")


def test_a_promoted_member_gets_admin_routes_at_once() -> None:
    accounts = KnownAccounts(VIEWER)
    headers = bearer(VIEWER)
    with real_token_client(accounts) as api:
        accounts.members[(VIEWER.user_id, VIEWER.workspace_id)] = TEST_USER  # now an admin
        response = api.get("/admin/members", headers=headers)

    assert response.status_code == 200


def test_a_removed_member_is_logged_out_of_that_workspace_at_once() -> None:
    accounts = KnownAccounts(TEST_USER)
    headers = bearer(TEST_USER)
    with real_token_client(accounts) as api:
        accounts.members.clear()  # removed (or the account disabled)
        response = api.get("/auth/me", headers=headers)

    # A 401: the web refreshes, and the refresh takes them to their own workspace.
    assert (response.status_code, response.json()["error"]) == (401, "not_authenticated")


def test_a_token_for_another_workspace_isnt_a_membership_there() -> None:
    accounts = KnownAccounts(TEST_USER)
    elsewhere = TEST_USER.model_copy(update={"workspace_id": uuid4()})
    with real_token_client(accounts) as api:
        response = api.get("/admin/members", headers=bearer(elsewhere))

    assert response.status_code == 401


def test_without_an_accounts_store_a_valid_token_is_a_503_not_trusted() -> None:
    with real_token_client(None) as api:
        valid = api.get("/admin/members", headers=bearer(TEST_USER))
        bad = api.get("/admin/members", headers={"Authorization": "Bearer nope"})

    # Fail closed: the role in the token is never used on its own...
    assert (valid.status_code, valid.json()["error"]) == (503, "accounts_unavailable")
    # ...and a bad token is still a 401, before any lookup.
    assert bad.status_code == 401
