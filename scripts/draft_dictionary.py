"""Draft the data dictionary with the LLM: a description of every table and column (3.2).

    uv run python scripts/draft_dictionary.py                      # every layer, missing tables
    uv run python scripts/draft_dictionary.py --layer semantic_layer/chinook.yaml
    uv run python scripts/draft_dictionary.py --tables customers orders --redo
    uv run python scripts/draft_dictionary.py --provider gemini --delay 5

Writes semantic_layer/<name>.dictionary.yaml next to each layer, with `reviewed: false`. Then
a person reviews it: fix what's wrong, set `reviewed: true` (never overwritten again).

One LLM call per table (~1,500 tokens). By default only tables not drafted yet; `--redo` also
redrafts unreviewed ones. The file is saved after every table, so a failure loses nothing.
"""

import argparse
import sys
import time
from pathlib import Path

import psycopg

from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import introspect_schema
from querymind.llm import LLMError, create_llm_provider
from querymind.semantic.dictionary import (
    dictionary_path,
    draft_table,
    load_dictionary,
    merge_drafts,
    save_dictionary,
)
from querymind.semantic.loader import SemanticLayerError, check_against, load_semantic_layer


def main() -> int:
    parser = argparse.ArgumentParser(description="Draft the data dictionary with the LLM.")
    parser.add_argument("--layer", type=Path, nargs="+", help="default: SEMANTIC_LAYER_PATHS")
    parser.add_argument("--tables", nargs="+", help="only these tables")
    parser.add_argument("--redo", action="store_true", help="redraft unreviewed tables too")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="override LLM_PROVIDER")
    parser.add_argument("--delay", type=float, default=10.0, help="seconds between LLM calls")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        model, _ = settings.llm_credentials()
        with connect_readonly(settings) as conn:  # the agent's role: it sees what the agent sees
            schema = introspect_schema(conn)
    except (ConfigError, psycopg.Error, DatabaseUnavailableError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    llm = create_llm_provider(settings)
    tables = {table.name: table for table in schema.tables}
    failures = 0

    for layer_path in args.layer or settings.semantic_layer_paths:
        try:
            layer = load_semantic_layer(layer_path)
            check_against(layer, schema)
            path = dictionary_path(layer_path)
            dictionary = load_dictionary(path)
        except SemanticLayerError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"{layer.database}: {path}")
        for name in layer.table_scope:
            entry = dictionary.tables.get(name) if dictionary else None
            if args.tables and name not in args.tables:
                continue
            if entry and (entry.reviewed or not args.redo):
                print(f"  {name}: kept ({'reviewed' if entry.reviewed else 'already drafted'})")
                continue
            try:
                draft = draft_table(llm, tables[name], layer)
            except LLMError as exc:
                failures += 1
                print(f"  {name}: FAILED ({type(exc).__name__}): {exc}")
                continue
            dictionary = merge_drafts(
                dictionary,
                {name: draft},
                database=layer.database,
                schema=schema,
                generated_by=f"{settings.llm_provider} / {model}",
            )
            save_dictionary(path, dictionary)
            print(f"  {name}: drafted ({len(draft.columns)} columns)")
            time.sleep(args.delay)  # stay under the provider's tokens-per-minute limit
    if failures:
        print(f"\n{failures} table(s) failed: run again to retry just those.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
