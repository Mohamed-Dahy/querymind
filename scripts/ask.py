"""Ask QueryMind questions from the terminal (manual check; grows with each Phase 1 step).

    uv run python scripts/ask.py "How many orders did each country place?"
    uv run python scripts/ask.py                     # the 10 sample questions below
    uv run python scripts/ask.py --provider gemini   # the other provider

Step 1.2: prints the SQL the LLM writes. It does NOT run it yet: generated SQL may only run
after the validator (1.3) has checked it, which the executor (1.4) will do.
"""

import argparse
import sys
import textwrap
import time

import psycopg

from querymind.agent.sql_generator import generate_sql
from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.schema import describe_schema, introspect_schema
from querymind.llm import LLMError, create_llm_provider

# Simple Northwind questions for the 1.2 manual check: filters, joins, aggregations, top-N.
SAMPLE_QUESTIONS = [
    "How many customers are there?",
    "List all products in the Beverages category.",
    "Which customers are based in Germany?",
    "How many orders did each country place? Sort from most to fewest.",
    "What are the 5 most expensive products?",
    "Which employee handled the most orders?",
    "What was the total freight cost per shipper?",
    "How many orders were placed in 1997?",
    "Which products are out of stock?",
    "What is the total sales amount per category?",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn questions into SQL with the configured LLM.")
    parser.add_argument("questions", nargs="*", help="questions to ask (default: 10 samples)")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="override LLM_PROVIDER")
    args = parser.parse_args()
    # The Windows console (cp1252) cannot print some characters LLMs like to use, such as the
    # narrow no-break space (U+202F). Print "?" for those instead of crashing.
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        with connect_readonly(settings) as conn:
            schema_text = describe_schema(introspect_schema(conn))
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    llm = create_llm_provider(settings)

    failures = 0
    for number, question in enumerate(args.questions or SAMPLE_QUESTIONS, start=1):
        print(f"[{number}] {question}")
        started = time.perf_counter()
        try:
            draft = generate_sql(llm, schema_text=schema_text, question=question)
        except (LLMError, ValueError) as exc:
            failures += 1
            print(f"    FAILED ({type(exc).__name__}): {exc}\n")
            continue
        print("    sql:")
        print(textwrap.indent(draft.sql, " " * 8))
        print(f"    explanation: {draft.explanation}")
        print(f"    tables_used: {', '.join(draft.tables_used)}")
        print(f"    ({llm.name}, {time.perf_counter() - started:.1f}s)\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
