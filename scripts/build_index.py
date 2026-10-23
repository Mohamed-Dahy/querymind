"""Build or refresh the schema index that schema retrieval searches (3.3).

    uv run python scripts/build_index.py                     # Gemini embeddings (GOOGLE_API_KEY)
    uv run python scripts/build_index.py --fake-embeddings   # no key: CI and offline tests only

One document per table and per metric (semantic/documents.py), from the schema the read-only
role sees, the semantic layers (SEMANTIC_LAYER_PATHS) and their data dictionaries. Only new or
changed documents are embedded, so running it again after a small edit costs one short call.

Run it again after changing a semantic layer, a dictionary, or the database tables. The app
warns at its first question when the index is out of date.

Writes as the admin role (it creates the `querymind` schema and the pgvector extension), then
lets the read-only role search it.
"""

import argparse
import sys

import psycopg

from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import introspect_schema
from querymind.llm import LLMProviderError
from querymind.llm.embeddings import EmbeddingProvider, FakeEmbeddings
from querymind.llm.factory import create_embedding_provider
from querymind.semantic.dictionary import load_dictionaries
from querymind.semantic.documents import build_documents
from querymind.semantic.index import ensure_index, upsert_documents
from querymind.semantic.loader import SemanticLayerError, check_against, load_semantic_layers


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the schema index for schema retrieval.")
    parser.add_argument(
        "--fake-embeddings",
        action="store_true",
        help="word-hash vectors instead of Gemini (tests/CI; the app won't use this index)",
    )
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        settings = get_settings()
        embeddings: EmbeddingProvider | None = (
            FakeEmbeddings() if args.fake_embeddings else create_embedding_provider(settings)
        )
        if embeddings is None:
            print("Error: GOOGLE_API_KEY is not set (or use --fake-embeddings).", file=sys.stderr)
            return 1
        layers = load_semantic_layers(settings.semantic_layer_paths)
        dictionaries = load_dictionaries(settings.semantic_layer_paths)
        with connect_readonly(settings) as conn:  # only what the agent may read gets indexed
            schema = introspect_schema(conn)
        for layer in layers:
            check_against(layer, schema)
        docs = build_documents(schema, layers, dictionaries)
        # One transaction as admin: the index changes completely, or not at all.
        with psycopg.connect(settings.admin_conninfo()) as conn:
            ensure_index(conn, reader=settings.analyst_ro_user)
            embedded, deleted = upsert_documents(conn, docs, embeddings)
    except (ConfigError, SemanticLayerError, LLMProviderError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    kinds = {kind: sum(d.kind == kind for d in docs) for kind in ("table", "metric")}
    print(
        f"Index: {len(docs)} documents ({kinds['table']} tables, {kinds['metric']} metrics), "
        f"model {embeddings.name}"
    )
    print(
        f"  embedded {embedded} new or changed, deleted {deleted}, {len(docs) - embedded} unchanged"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
