"""Accounts on the real database (5.1): sign-up, logins and refresh-token rotation.

Needs the app's storage: `uv run python data/setup_app_db.py` with APP_DB_USER/APP_DB_PASSWORD.
"""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors
from setup_app_db import SCHEMA, give_unowned_rows_to

from querymind.auth.errors import EmailTakenError, NotAuthenticatedError
from querymind.auth.passwords import hash_password, verify_password
from querymind.auth.tokens import hash_refresh_token, new_refresh_token
from querymind.config import ConfigError, Settings
from querymind.db.connection import connect_app
from querymind.store.accounts import Account, AccountStore

pytestmark = pytest.mark.integration

LATER = datetime.now(UTC) + timedelta(days=14)


@pytest.fixture
def accounts(settings: Settings) -> AccountStore:
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: no app storage (see .env.example)")
    return AccountStore(settings)


@pytest.fixture
def app_conn(settings: Settings, accounts: AccountStore) -> Iterator[psycopg.Connection]:
    """As the app's role, autocommit: what a test changes here is seen by the store at once."""
    with connect_app(settings) as conn:
        conn.autocommit = True
        yield conn


@pytest.fixture
def account(accounts: AccountStore, app_conn: psycopg.Connection) -> Iterator[Account]:
    """A fresh account with the password "a long enough password", deleted after the test."""
    made = accounts.sign_up(
        f"ana-{uuid4()}@example.com", hash_password("a long enough password"), workspace_name="Ana"
    )
    yield made
    delete(app_conn, made)


def delete(conn: psycopg.Connection, account: Account) -> None:
    conn.execute(f"DELETE FROM {SCHEMA}.users WHERE id = %s", (account.user_id,))
    conn.execute(f"DELETE FROM {SCHEMA}.workspaces WHERE id = %s", (account.workspace_id,))


def logged_in(accounts: AccountStore, account: Account) -> str:
    """Start a login; returns its refresh token."""
    token = new_refresh_token()
    accounts.start_session(account, hash_refresh_token(token), LATER)
    return token


def rotate(accounts: AccountStore, token: str) -> str:
    """Use a refresh token; returns the new one."""
    new = new_refresh_token()
    accounts.rotate(hash_refresh_token(token), hash_refresh_token(new), LATER)
    return new


# --- Sign-up and login --------------------------------------------------------------------------


def test_sign_up_makes_the_user_an_admin_of_their_own_workspace(
    accounts: AccountStore, account: Account
) -> None:
    assert account.role == "admin" and account.workspace_name == "Ana"
    found = accounts.find_login(account.email)

    assert found is not None
    login, stored_hash = found
    assert login == account
    assert verify_password(stored_hash, "a long enough password")
    assert accounts.account(account.user_id, account.workspace_id) == account


def test_an_email_has_one_account_and_a_failed_sign_up_leaves_nothing(
    accounts: AccountStore, account: Account, app_conn: psycopg.Connection
) -> None:
    def workspaces() -> int:
        row = app_conn.execute(f"SELECT count(*) FROM {SCHEMA}.workspaces").fetchone()
        return row[0] if row else -1

    before = workspaces()
    with pytest.raises(EmailTakenError):
        accounts.sign_up(account.email, hash_password("x" * 12), workspace_name="Again")

    assert workspaces() == before  # one transaction: no orphan workspace


def test_the_database_only_takes_lowercased_emails(app_conn: psycopg.Connection) -> None:
    with pytest.raises(errors.CheckViolation):
        app_conn.execute(
            f"INSERT INTO {SCHEMA}.users (email, password_hash) VALUES ('Ana@X.com', 'h')"
        )


def test_no_account_and_disabled_accounts_cant_log_in(
    accounts: AccountStore, account: Account, app_conn: psycopg.Connection
) -> None:
    assert accounts.find_login(f"nobody-{uuid4()}@example.com") is None
    app_conn.execute(f"UPDATE {SCHEMA}.users SET disabled = true WHERE id = %s", (account.user_id,))

    assert accounts.find_login(account.email) is None
    assert accounts.account(account.user_id, account.workspace_id) is None


# --- Refresh tokens -----------------------------------------------------------------------------


def test_a_refresh_token_is_stored_only_as_a_hash(
    accounts: AccountStore, account: Account, app_conn: psycopg.Connection
) -> None:
    token = logged_in(accounts, account)

    rows = app_conn.execute(
        f"SELECT token_hash FROM {SCHEMA}.refresh_tokens WHERE user_id = %s", (account.user_id,)
    ).fetchall()
    assert rows == [(hash_refresh_token(token),)]


def test_each_refresh_replaces_the_token(accounts: AccountStore, account: Account) -> None:
    first = logged_in(accounts, account)
    second = rotate(accounts, first)
    third = rotate(accounts, second)

    assert len({first, second, third}) == 3


def test_a_reused_token_revokes_the_whole_login(
    accounts: AccountStore, account: Account, app_conn: psycopg.Connection
) -> None:
    # A thief copies the cookie; the real user refreshes first, then the thief tries the copy.
    stolen = logged_in(accounts, account)
    users_new = rotate(accounts, stolen)
    app_conn.execute(  # past the grace window for two tabs refreshing at once
        f"UPDATE {SCHEMA}.refresh_tokens SET used_at = now() - interval '1 minute' "
        "WHERE token_hash = %s",
        (hash_refresh_token(stolen),),
    )

    with pytest.raises(NotAuthenticatedError):
        rotate(accounts, stolen)
    # The revocation was committed, and it took the user's newer token too: both must log in again.
    with pytest.raises(NotAuthenticatedError):
        rotate(accounts, users_new)


def test_two_tabs_refreshing_at_once_both_stay_logged_in(
    accounts: AccountStore, account: Account
) -> None:
    token = logged_in(accounts, account)
    tab_one = rotate(accounts, token)
    tab_two = rotate(accounts, token)  # the same cookie, a moment later

    rotate(accounts, tab_one)
    rotate(accounts, tab_two)


def test_other_logins_are_left_alone(accounts: AccountStore, account: Account) -> None:
    laptop = logged_in(accounts, account)
    phone = logged_in(accounts, account)

    accounts.end_session(hash_refresh_token(laptop))

    with pytest.raises(NotAuthenticatedError):
        rotate(accounts, laptop)
    rotate(accounts, phone)  # a different family: still logged in


@pytest.mark.parametrize(
    "spoil",
    [
        "UPDATE {schema}.refresh_tokens SET expires_at = now() - interval '1 second' "
        "WHERE user_id = %s",
        "UPDATE {schema}.users SET disabled = true WHERE id = %s",
        "DELETE FROM {schema}.memberships WHERE user_id = %s",
    ],
    ids=["expired", "user disabled", "removed from the workspace"],
)
def test_a_refresh_fails_when_the_login_is_no_longer_valid(
    accounts: AccountStore, account: Account, app_conn: psycopg.Connection, spoil: str
) -> None:
    token = logged_in(accounts, account)
    app_conn.execute(spoil.format(schema=SCHEMA).encode(), (account.user_id,))

    with pytest.raises(NotAuthenticatedError):
        rotate(accounts, token)


def test_an_unknown_token_is_refused_and_logout_with_it_is_fine(accounts: AccountStore) -> None:
    with pytest.raises(NotAuthenticatedError):
        rotate(accounts, new_refresh_token())
    accounts.end_session(hash_refresh_token(new_refresh_token()))


# --- Conversations saved before logins (setup_app_db.py --owner) ---------------------------------


def test_unowned_rows_go_only_to_the_named_owner(settings: Settings, account: Account) -> None:
    # All in one admin transaction, rolled back: real conversations saved before logins (on a
    # developer's database) must not be handed to this throwaway account, which is then deleted.
    conversation = uuid4()
    with psycopg.connect(settings.admin_conninfo()) as admin:
        admin.execute(
            f"INSERT INTO {SCHEMA}.conversations (id, title) VALUES (%s, 'Before logins')",
            (conversation,),
        )
        changed = give_unowned_rows_to(admin, account.email.upper())
        owner = admin.execute(
            f"SELECT user_id, workspace_id FROM {SCHEMA}.conversations WHERE id = %s",
            (conversation,),
        ).fetchone()
        with pytest.raises(ConfigError):
            give_unowned_rows_to(admin, f"nobody-{uuid4()}@example.com")
        admin.rollback()

    assert changed >= 1
    assert owner == (account.user_id, account.workspace_id)
