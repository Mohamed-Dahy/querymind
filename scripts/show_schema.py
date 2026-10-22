"""Print the schema description exactly as the LLM will see it, plus its size.

    uv run python scripts/show_schema.py

Connects as the read-only role, like the agent does. Needs the database up and seeded.
"""

import sys

import psycopg

from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import (
    SCHEMA_TOKEN_BUDGET,
    describe_schema,
    estimate_tokens,
    introspect_schema,
)


def main() -> int:
    try:
        with connect_readonly(get_settings()) as conn:
            schema = introspect_schema(conn)
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    text = describe_schema(schema)
    print(text)
    print(
        f"\n--- {len(schema.tables)} tables, {len(text)} chars, "
        f"~{estimate_tokens(text)} tokens (budget {SCHEMA_TOKEN_BUDGET})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
