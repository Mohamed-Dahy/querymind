"""Workspace members on the real database (5.12): adding, re-roling, removing, the last-admin
guard under concurrency, switching workspaces, and the whole flow over HTTP.

Needs the app's storage: `uv run python data/setup_app_db.py` with APP_DB_USER/APP_DB_PASSWORD.
"""

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.auth import CookieSettings
from querymind.api.main import create_app
from querymind.auth.errors import NotAuthenticatedError
from querymind.auth.tokens import hash_refresh_token, new_refresh_token
from querymind.config import Settings
from querymind.db.connection import connect_app
from querymind.store.accounts import Account, AccountStore
from querymind.store.audit import AuditStore
from querymind.store.errors import (
    AccountNotFoundError,
    AlreadyMemberError,
    LastAdminError,
    MemberNotFoundError,
)
from querymind.store.members import MemberStore
from querymind.store.row_scope import RowScopeStore
from tests.auth_helpers import make_tokens

pytestmark = pytest.mark.integration

LATER = datetime.now(UTC) + timedelta(days=14)
PASSWORD = "a long enough password"

SignUp = Callable[[], Account]


@pytest.fixture
def accounts(settings: Settings) -> AccountStore:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: no app storage (see .env.example)")
    return AccountStore(settings)


@pytest.fixture
def members(settings: Settings, accounts: AccountStore) -> MemberStore:
    return MemberStore(settings)


@pytest.fixture
def sign_up(settings: Settings, accounts: AccountStore) -> Iterator[SignUp]:
    """Throwaway accounts (each with its own workspace), deleted after the test."""
    made: list[Account] = []

    def make() -> Account:
        account = accounts.sign_up(
            f"member-{uuid4()}@example.com", "not-a-real-hash", workspace_name="Test"
        )
        made.append(account)
        return account

    yield make
    with connect_app(settings) as conn:
        for account in made:
            conn.execute("DELETE FROM querymind_app.users WHERE id = %s", (account.user_id,))
            conn.execute(
                "DELETE FROM querymind_app.workspaces WHERE id = %s", (account.workspace_id,)
            )


def test_an_admin_adds_lists_reroles_and_removes_a_member(
    members: MemberStore, sign_up: SignUp
) -> None:
    admin, ana = sign_up(), sign_up()
    workspace = admin.workspace_id

    added = members.add(workspace, ana.email.upper(), "viewer")  # any case, as sign-up stores it
    assert (added.user_id, added.role, added.row_scope) == (ana.user_id, "viewer", None)
    assert [(m.email, m.role) for m in members.list(workspace)] == [
        (admin.email, "admin"),
        (ana.email, "viewer"),
    ]

    after, before = members.set_role(workspace, ana.user_id, "analyst")
    assert (before, after.role) == ("viewer", "analyst")

    removed = members.remove(workspace, ana.user_id)
    assert removed.role == "analyst"
    assert [m.email for m in members.list(workspace)] == [admin.email]
    # Their own workspace is untouched.
    assert [m.role for m in members.list(ana.workspace_id)] == ["admin"]


def test_only_a_signed_up_enabled_user_can_be_added_once(
    settings: Settings, members: MemberStore, sign_up: SignUp
) -> None:
    admin, ana = sign_up(), sign_up()
    members.add(admin.workspace_id, ana.email, "viewer")

    with pytest.raises(AlreadyMemberError):
        members.add(admin.workspace_id, ana.email, "analyst")
    with pytest.raises(AccountNotFoundError):
        members.add(admin.workspace_id, f"nobody-{uuid4()}@example.com", "viewer")
    other = sign_up()
    with connect_app(settings) as conn:
        conn.execute(
            "UPDATE querymind_app.users SET disabled = true WHERE id = %s", (other.user_id,)
        )
    with pytest.raises(AccountNotFoundError):
        members.add(admin.workspace_id, other.email, "viewer")


def test_promotion_clears_the_row_scope_and_demotion_starts_with_every_row(
    settings: Settings, members: MemberStore, sign_up: SignUp
) -> None:
    admin, ana = sign_up(), sign_up()
    scopes = RowScopeStore(settings)
    members.add(admin.workspace_id, ana.email, "viewer")
    scopes.set(admin.workspace_id, ana.user_id, ("Eastern",))

    promoted, _ = members.set_role(admin.workspace_id, ana.user_id, "admin")
    demoted, _ = members.set_role(admin.workspace_id, ana.user_id, "viewer")

    assert promoted.row_scope is None  # admins see every row
    assert demoted.row_scope is None  # until an admin sets a scope again


def test_the_last_admin_can_be_neither_demoted_nor_removed(
    members: MemberStore, sign_up: SignUp
) -> None:
    admin, ana = sign_up(), sign_up()
    workspace = admin.workspace_id
    members.add(workspace, ana.email, "viewer")

    with pytest.raises(LastAdminError):
        members.set_role(workspace, admin.user_id, "analyst")
    with pytest.raises(LastAdminError):
        members.remove(workspace, admin.user_id)
    with pytest.raises(MemberNotFoundError):
        members.remove(workspace, uuid4())

    # With a second admin, the first may step down.
    members.set_role(workspace, ana.user_id, "admin")
    members.set_role(workspace, admin.user_id, "viewer")
    assert {m.email: m.role for m in members.list(workspace)} == {
        admin.email: "viewer",
        ana.email: "admin",
    }


def test_a_disabled_admin_doesnt_count_as_the_other_admin(
    settings: Settings, members: MemberStore, sign_up: SignUp
) -> None:
    admin, ana = sign_up(), sign_up()
    members.add(admin.workspace_id, ana.email, "admin")
    with connect_app(settings) as conn:
        conn.execute("UPDATE querymind_app.users SET disabled = true WHERE id = %s", (ana.user_id,))

    with pytest.raises(LastAdminError):
        members.remove(admin.workspace_id, admin.user_id)


def test_two_admins_demoting_each_other_at_once_leave_one_admin(
    members: MemberStore, sign_up: SignUp
) -> None:
    """The race the row locks prevent: without them, both would see "another admin stays"."""
    first, second = sign_up(), sign_up()
    workspace = first.workspace_id
    members.add(workspace, second.email, "admin")
    outcome: dict[str, Any] = {}

    def demote_first() -> None:
        try:
            members.set_role(workspace, first.user_id, "viewer")
            outcome["second change"] = "done"
        except LastAdminError:
            outcome["second change"] = "refused"

    # The first change: inside its transaction, the admin rows are locked...
    with members._transaction() as conn:
        members._guarded(conn, workspace, second.user_id, keeps_admin=False)
        conn.execute(
            """
            UPDATE querymind_app.memberships SET role = 'viewer'
            WHERE workspace_id = %s AND user_id = %s
            """,
            (workspace, second.user_id),
        )
        # ...so the other change, started now, has to wait for it.
        other = threading.Thread(target=demote_first)
        other.start()
        time.sleep(0.5)
        assert other.is_alive(), "the second change should be waiting on the lock"
    # First committed: the second one counts again and finds itself the last admin.
    other.join(timeout=10)

    assert outcome == {"second change": "refused"}
    assert {m.user_id: m.role for m in members.list(workspace)} == {
        first.user_id: "admin",
        second.user_id: "viewer",
    }


# --- Switching workspaces (store/accounts.py) ---


def login(accounts: AccountStore, account: Account) -> str:
    """A refresh token for a new login of `account`, as /auth/login makes it."""
    token = new_refresh_token()
    accounts.start_session(account, hash_refresh_token(token), LATER)
    return token


def use(accounts: AccountStore, token: str) -> tuple[str, Account]:
    """A refresh: the new token and the account it is for."""
    new = new_refresh_token()
    return new, accounts.rotate(hash_refresh_token(token), hash_refresh_token(new), LATER)


def test_workspaces_lists_your_own_first(
    accounts: AccountStore, members: MemberStore, sign_up: SignUp
) -> None:
    ana, bob = sign_up(), sign_up()
    members.add(bob.workspace_id, ana.email, "viewer")

    found = accounts.workspaces(ana.user_id)

    assert [(w.workspace_id, w.role) for w in found] == [
        (ana.workspace_id, "admin"),
        (bob.workspace_id, "viewer"),
    ]


def test_a_switch_moves_the_login_and_the_next_refresh_stays_there(
    accounts: AccountStore, members: MemberStore, sign_up: SignUp
) -> None:
    ana, bob = sign_up(), sign_up()
    members.add(bob.workspace_id, ana.email, "viewer")
    token = login(accounts, ana)

    new = new_refresh_token()
    switched = accounts.switch(
        hash_refresh_token(token),
        hash_refresh_token(new),
        LATER,
        user_id=ana.user_id,
        workspace_id=bob.workspace_id,
    )
    _, reloaded = use(accounts, new)

    assert (switched.workspace_id, switched.role) == (bob.workspace_id, "viewer")
    assert reloaded.workspace_id == bob.workspace_id
    # Logging out names the workspace the login is in now.
    ended = accounts.end_session(hash_refresh_token(new))
    assert ended is not None and ended[1] == bob.workspace_id


def test_a_switch_to_a_workspace_youre_not_in_changes_nothing(
    accounts: AccountStore, sign_up: SignUp
) -> None:
    ana, bob = sign_up(), sign_up()
    token = login(accounts, ana)

    with pytest.raises(MemberNotFoundError):
        accounts.switch(
            hash_refresh_token(token),
            hash_refresh_token(new_refresh_token()),
            LATER,
            user_id=ana.user_id,
            workspace_id=bob.workspace_id,
        )
    _, still = use(accounts, token)  # the cookie wasn't used up

    assert still.workspace_id == ana.workspace_id


def test_a_switch_with_someone_elses_cookie_is_refused(
    accounts: AccountStore, members: MemberStore, sign_up: SignUp
) -> None:
    ana, bob = sign_up(), sign_up()
    members.add(bob.workspace_id, ana.email, "viewer")
    bobs_cookie = login(accounts, bob)

    with pytest.raises(NotAuthenticatedError):
        accounts.switch(
            hash_refresh_token(bobs_cookie),
            hash_refresh_token(new_refresh_token()),
            LATER,
            user_id=ana.user_id,
            workspace_id=bob.workspace_id,
        )


def test_removed_from_a_workspace_the_next_refresh_lands_in_your_own(
    accounts: AccountStore, members: MemberStore, sign_up: SignUp
) -> None:
    ana, bob = sign_up(), sign_up()
    members.add(bob.workspace_id, ana.email, "viewer")
    token = login(accounts, ana)
    new = new_refresh_token()
    accounts.switch(
        hash_refresh_token(token),
        hash_refresh_token(new),
        LATER,
        user_id=ana.user_id,
        workspace_id=bob.workspace_id,
    )

    members.remove(bob.workspace_id, ana.user_id)
    _, landed = use(accounts, new)

    assert (landed.workspace_id, landed.role) == (ana.workspace_id, "admin")


# --- The done-when, over HTTP ---


@pytest.fixture
def api(settings: Settings, accounts: AccountStore, members: MemberStore) -> Iterator[TestClient]:
    app = create_app(
        pipeline=object(),  # type: ignore[arg-type]
        accounts=accounts,
        members=members,
        audit=AuditStore(settings),
        tokens=make_tokens(),
        auth_cookie=CookieSettings(path="/auth", secure=False, days=14),
    )
    with TestClient(app, raise_server_exceptions=False) as client:  # `with` runs the startup
        yield client


@pytest.fixture
def emails(settings: Settings) -> Iterator[list[str]]:
    """Addresses signed up over HTTP; their accounts and workspaces are deleted afterwards."""
    made: list[str] = []
    yield made
    with connect_app(settings) as conn:
        for address in made:
            conn.execute(
                """
                DELETE FROM querymind_app.workspaces WHERE id IN (
                    SELECT m.workspace_id FROM querymind_app.memberships AS m
                    JOIN querymind_app.users AS u ON u.id = m.user_id
                    WHERE u.email = %s AND m.role = 'admin'
                )
                """,
                (address,),
            )
            conn.execute("DELETE FROM querymind_app.users WHERE email = %s", (address,))


def signed_up(api: TestClient, emails: list[str]) -> tuple[str, dict[str, Any]]:
    address = f"flow-{uuid4()}@example.com"
    emails.append(address)
    response = api.post("/auth/signup", json={"email": address, "password": PASSWORD})
    assert response.status_code == 201
    return address, response.json()


def bearer(body: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {body['access_token']}"}


def test_done_when_an_admin_adds_a_viewer_who_switches_in_and_back(
    settings: Settings, api: TestClient, emails: list[str]
) -> None:
    admin_email, admin = signed_up(api, emails)
    viewer_email, viewer = signed_up(api, emails)  # the client now holds the viewer's cookie
    admins_workspace = admin["user"]["workspace"]["id"]

    added = api.post(
        "/admin/members", json={"email": viewer_email, "role": "viewer"}, headers=bearer(admin)
    )
    assert added.status_code == 201

    # The viewer logs in: a login starts in their own workspace, and they now see two.
    login = api.post("/auth/login", json={"email": viewer_email, "password": PASSWORD}).json()
    own_workspace = viewer["user"]["workspace"]["id"]
    assert (login["user"]["workspace"]["id"], login["user"]["role"]) == (own_workspace, "admin")
    listed = api.get("/auth/workspaces", headers=bearer(login)).json()["workspaces"]
    assert [(w["role"], w["current"]) for w in listed] == [("admin", True), ("viewer", False)]

    # Into the admin's workspace, as a viewer: admin routes are closed there.
    inside = api.post(
        "/auth/switch", json={"workspace_id": admins_workspace}, headers=bearer(login)
    ).json()
    assert inside["user"]["role"] == "viewer"
    assert api.get("/admin/members", headers=bearer(inside)).status_code == 403
    listed = api.get("/auth/workspaces", headers=bearer(inside)).json()["workspaces"]
    assert [w["current"] for w in listed] == [False, True]

    # And back to their own (with the cookie the first switch issued), then in again.
    home = api.post(
        "/auth/switch", json={"workspace_id": own_workspace}, headers=bearer(inside)
    ).json()
    assert (home["user"]["workspace"]["id"], home["user"]["role"]) == (own_workspace, "admin")
    inside = api.post(
        "/auth/switch", json={"workspace_id": admins_workspace}, headers=bearer(home)
    ).json()
    assert inside["user"]["role"] == "viewer"

    # The admin promotes them: their very next request has the new role (same token).
    viewer_id = added.json()["user_id"]
    api.patch(f"/admin/members/{viewer_id}", json={"role": "admin"}, headers=bearer(admin))
    assert api.get("/admin/members", headers=bearer(inside)).status_code == 200

    # Removed: the token stops at once, and the next refresh lands them back in their own.
    api.delete(f"/admin/members/{viewer_id}", headers=bearer(admin))
    assert api.get("/auth/me", headers=bearer(inside)).status_code == 401
    back = api.post("/auth/refresh").json()
    assert back["user"]["workspace"]["id"] == viewer["user"]["workspace"]["id"]
    assert back["user"]["role"] == "admin"

    # Every step is in the admin's workspace's audit log.
    lines, _ = AuditStore(settings).list(UUID(admins_workspace))
    actions = [line.action for line in lines]
    for action in ("member_added", "role_changed", "member_removed", "workspace_switched"):
        assert action in actions
    assert admin_email in {line.email for line in lines}
