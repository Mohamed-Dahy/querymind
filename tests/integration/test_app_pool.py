"""The app-database pool (Step 0) on the real database: reuse, transactions, a full pool, and a
connection the database dropped.

Needs the app's storage: `uv run python data/setup_app_db.py` with APP_DB_USER/APP_DB_PASSWORD.
"""

import threading
import time
from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest

from querymind.config import Settings
from querymind.db.connection import AppPool, connect_app
from querymind.store.accounts import AccountStore
from querymind.store.errors import AccountsUnavailableError

pytestmark = pytest.mark.integration


@pytest.fixture
def pool(settings: Settings) -> Iterator[AppPool]:
    """One connection only, and a short wait, so "busy" is easy to make."""
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: no app storage (see .env.example)")
    pool = AppPool(settings, min_size=1, max_size=1, timeout=0.5)
    pool.open()
    yield pool
    pool.close()


def backend_pid(pool: AppPool, settings: Settings) -> int:
    with pool.connect(settings) as conn:
        row = conn.execute("SELECT pg_backend_pid()").fetchone()
    assert row is not None
    return int(row[0])


def test_a_connection_is_reused_not_reopened(pool: AppPool, settings: Settings) -> None:
    assert backend_pid(pool, settings) == backend_pid(pool, settings)


def test_a_block_commits_and_an_error_rolls_back(pool: AppPool, settings: Settings) -> None:
    """The same promise as `with connect_app(settings) as conn:`, which every store relies on."""
    kept, dropped = f"pool-{uuid4()}", f"pool-{uuid4()}"
    try:
        with pool.connect(settings) as conn:
            conn.execute("INSERT INTO querymind_app.workspaces (name) VALUES (%s)", (kept[:100],))
        with pytest.raises(RuntimeError), pool.connect(settings) as conn:
            conn.execute(
                "INSERT INTO querymind_app.workspaces (name) VALUES (%s)", (dropped[:100],)
            )
            raise RuntimeError("the store raised")
        with connect_app(settings) as other:  # seen from outside the pool
            names = {
                row[0]
                for row in other.execute(
                    "SELECT name FROM querymind_app.workspaces WHERE name IN (%s, %s)",
                    (kept[:100], dropped[:100]),
                )
            }
        assert names == {kept[:100]}
    finally:
        with connect_app(settings) as conn:
            conn.execute("DELETE FROM querymind_app.workspaces WHERE name = %s", (kept[:100],))


def test_a_store_works_through_the_pool(pool: AppPool, settings: Settings) -> None:
    accounts = AccountStore(settings, connect=pool.connect)
    account = accounts.sign_up(f"pool-{uuid4()}@example.com", "not-a-hash", workspace_name="Pool")
    try:
        assert accounts.account(account.user_id, account.workspace_id) == account
    finally:
        with connect_app(settings) as conn:
            conn.execute("DELETE FROM querymind_app.users WHERE id = %s", (account.user_id,))
            conn.execute(
                "DELETE FROM querymind_app.workspaces WHERE id = %s", (account.workspace_id,)
            )


def test_a_full_pool_is_the_stores_clean_503_after_the_wait(
    pool: AppPool, settings: Settings
) -> None:
    accounts = AccountStore(settings, connect=pool.connect)
    taken, release = threading.Event(), threading.Event()

    def hold_the_only_connection() -> None:
        with pool.connect(settings):
            taken.set()
            release.wait(5)

    holder = threading.Thread(target=hold_the_only_connection)
    holder.start()
    try:
        assert taken.wait(5)
        started = time.monotonic()
        with pytest.raises(AccountsUnavailableError):
            accounts.account(uuid4(), uuid4())
        assert 0.4 < time.monotonic() - started < 3  # waited ~timeout, then gave up
    finally:
        release.set()
        holder.join(5)
    # Given back: the next request gets it at once.
    assert accounts.account(uuid4(), uuid4()) is None


def test_a_connection_the_database_dropped_is_replaced(pool: AppPool, settings: Settings) -> None:
    """A database restart (or an idle timeout) kills pooled connections; the check on the way out
    notices and opens a new one, so the request doesn't fail."""
    first = backend_pid(pool, settings)
    with psycopg.connect(settings.admin_conninfo(), autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(%s)", (first,))
    time.sleep(0.2)

    second = backend_pid(pool, settings)

    assert second != first
