"""Shared fixtures for integration tests. They need the Docker database, seeded:

docker compose up -d --wait
uv run python data/seed_northwind.py
uv run pytest -m integration
"""

from collections.abc import Iterator

import psycopg
import pytest

from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly


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
