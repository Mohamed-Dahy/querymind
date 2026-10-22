"""Print the schema description exactly as the LLM will see it, plus its size.

    uv run python scripts/show_schema.py

Connects as the read-only role, like the agent does. Needs the database up and seeded.
Includes the business definitions of the semantic layer (SEMANTIC_LAYER_PATH), as the agent does.
"""

import sys

import psycopg

from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SCHEMA_TOKEN_BUDGET, estimate_tokens, introspect_schema
from querymind.semantic.loader import SemanticLayerError, check_against, load_semantic_layer
from querymind.semantic.render import schema_prompt_text


def main() -> int:
    try:
        settings = get_settings()
        layer = (
            load_semantic_layer(settings.semantic_layer_path)
            if settings.semantic_layer_path
            else None
        )
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn)
        if layer is not None:
            check_against(layer, schema)
    except (ConfigError, SemanticLayerError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    text = schema_prompt_text(schema, layer)
    print(text)
    print(
        f"\n--- {len(schema.tables)} tables, {len(text)} chars, "
        f"~{estimate_tokens(text)} tokens (budget {SCHEMA_TOKEN_BUDGET})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
