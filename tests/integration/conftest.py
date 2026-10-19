"""Shared fixtures for integration tests. They need the Docker database, seeded:

docker compose up -d --wait
uv run --env-file .env python data/seed_northwind.py
uv run --env-file .env pytest -m integration
"""

import os
from collections.abc import Iterator

import psycopg
import pytest


def env(name: str) -> str:
    """Read a required environment variable, or fail the test with a clear hint."""
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. Run integration tests with: uv run --env-file .env pytest",
            pytrace=False,
        )
    return value


def connect(user: str, password: str) -> psycopg.Connection:
    """Open a connection to the QueryMind database as the given role."""
    try:
        return psycopg.connect(
            host=env("POSTGRES_HOST"),
            port=int(env("POSTGRES_PORT")),
            dbname=env("POSTGRES_DB"),
            user=user,
            password=password,
            connect_timeout=5,
        )
    except psycopg.OperationalError as exc:
        pytest.fail(
            f"Cannot connect as {user!r}: {exc}\n"
            "Is the database up and seeded? docker compose up -d --wait, then run the seed script.",
            pytrace=False,
        )


@pytest.fixture
def ro_conn() -> Iterator[psycopg.Connection]:
    """Connection as the agent's read-only role (analyst_ro)."""
    conn = connect(env("ANALYST_RO_USER"), env("ANALYST_RO_PASSWORD"))
    try:
        yield conn
    finally:
        conn.close()  # closing without commit = anything the test did is rolled back
