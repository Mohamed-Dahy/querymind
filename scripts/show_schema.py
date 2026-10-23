"""Print the schema description exactly as the LLM will see it, plus its size.

    uv run python scripts/show_schema.py                                   # the whole schema
    uv run python scripts/show_schema.py --question "Top 3 genres by sales"   # retrieved (3.3)

Connects as the read-only role, like the agent does. Needs the database up and seeded.
Includes the business definitions of the semantic layers (SEMANTIC_LAYER_PATHS), as the agent does.
With --question: the tables schema retrieval picks for it (needs GOOGLE_API_KEY and the index
from scripts/build_index.py), whatever SCHEMA_RETRIEVAL says.
"""

import argparse
import sys

import psycopg

from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SCHEMA_TOKEN_BUDGET, estimate_tokens, introspect_schema
from querymind.llm import create_embedding_provider
from querymind.semantic.loader import SemanticLayerError, check_against, load_semantic_layers
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever


def main() -> int:
    parser = argparse.ArgumentParser(description="Print the schema text the LLM sees.")
    parser.add_argument("--question", help="show only the tables retrieval picks for this question")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        settings = get_settings()
        layers = load_semantic_layers(settings.semantic_layer_paths)
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn)
        for layer in layers:
            check_against(layer, schema)
    except (ConfigError, SemanticLayerError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    catalog = SchemaCatalog(schema, layers)
    if not args.question:
        text = catalog.full_text
        print(text)
        print(
            f"\n--- {len(schema.tables)} tables, {len(text)} chars, "
            f"~{estimate_tokens(text)} tokens (budget {SCHEMA_TOKEN_BUDGET})"
        )
        return 0

    embeddings = create_embedding_provider(settings)
    if embeddings is None:
        print("Error: GOOGLE_API_KEY is not set: retrieval needs embeddings.", file=sys.stderr)
        return 1
    retriever = SchemaRetriever(
        catalog=catalog, embeddings=embeddings, connect=connect_readonly, settings=settings
    )
    selection = retriever.select(args.question)
    info = selection.info
    print(selection.schema_text)
    full = estimate_tokens(catalog.full_text)
    print(f"\n--- {info.mode}: {len(info.tables)} of {len(schema.tables)} tables")
    print(f"    in the prompt: {', '.join(info.tables)}")
    print(f"    search hits: {', '.join(info.hits) or '-'}; added: {', '.join(info.added) or '-'}")
    if info.reason:
        print(f"    fell back because: {info.reason}")
    print(f"    ~{info.schema_tokens} tokens (whole schema ~{full})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
