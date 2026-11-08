"""Shared fixtures for integration tests. They need the Docker database, seeded:

docker compose up -d --wait
uv run python data/seed_northwind.py
uv run pytest -m integration
"""

from collections.abc import Iterator
from uuid import uuid4

import psycopg
import pytest

from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_app, connect_readonly
from querymind.store.accounts import AccountStore
from querymind.store.conversations import Owner


@pytest.fixture(scope="session")
def settings() -> Settings:
    """The real settings from .env (integration tests talk to the real database)."""
    try:
        return get_settings()
    except ConfigError as exc:
        pytest.fail(str(exc), pytrace=False)


@pytest.fixture
def ro_conn(settings: Settings) -> Iterator[psycopg.Connection]:
    """Connection as the agent's read-only role (analyst_ro)."""
    try:
        conn = connect_readonly(settings)
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"Cannot connect as {settings.analyst_ro_user!r}: {exc}\n"
            "Is the database up and seeded? docker compose up -d --wait, then run the seed script.",
            pytrace=False,
        )
    try:
        yield conn
    finally:
        conn.close()  # closing without commit = anything the test did is rolled back


def _throwaway_owner(settings: Settings) -> Iterator[Owner]:
    """A real account (users and workspaces are foreign keys), deleted again afterwards. Deleting it
    deletes its conversations and saved questions too (ON DELETE CASCADE)."""
    if not settings.history_enabled:
        pytest.skip("APP_DB_USER isn't set: no app storage (see .env.example)")
    accounts = AccountStore(settings)
    account = accounts.sign_up(
        f"test-{uuid4()}@example.com", "not-a-real-hash", workspace_name="Test"
    )
    try:
        yield Owner(user_id=account.user_id, workspace_id=account.workspace_id)
    finally:
        with connect_app(settings) as conn:
            conn.execute("DELETE FROM querymind_app.users WHERE id = %s", (account.user_id,))
            conn.execute(
                "DELETE FROM querymind_app.workspaces WHERE id = %s", (account.workspace_id,)
            )


@pytest.fixture
def owner(settings: Settings) -> Iterator[Owner]:
    """The logged-in user the store is asked for."""
    yield from _throwaway_owner(settings)


@pytest.fixture
def other_owner(settings: Settings) -> Iterator[Owner]:
    """Someone else, for "can't see another user's rows"."""
    yield from _throwaway_owner(settings)
