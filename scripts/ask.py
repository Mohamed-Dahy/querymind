"""Ask QueryMind questions from the terminal (manual check; grows with each Phase 1 step).

    uv run python scripts/ask.py "How many orders did each country place?"
    uv run python scripts/ask.py                     # the 10 sample questions below
    uv run python scripts/ask.py --provider gemini   # the other provider

Step 1.4: question → SQL (LLM) → validator → executor (as analyst_ro) → first rows printed.
The plain-language answer arrives in 1.5.
"""

import argparse
import sys
import textwrap
import time

import psycopg

from querymind.agent.sql_generator import generate_sql
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import QueryError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import SchemaInfo, describe_schema, introspect_schema
from querymind.llm import LLMError, LLMProvider, create_llm_provider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

# Simple Northwind questions for the manual check: filters, joins, aggregations, top-N.
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
PREVIEW_ROWS = 5  # rows printed per result


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask questions about the database.")
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
        llm = create_llm_provider(settings)
        # One read-only connection for the whole run: schema first, then every query.
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn)
            questions = args.questions or SAMPLE_QUESTIONS
            failures = sum(
                not ask(conn, llm, settings, schema, number, question)
                for number, question in enumerate(questions, start=1)
            )
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1 if failures else 0


def ask(
    conn: psycopg.Connection,
    llm: LLMProvider,
    settings: Settings,
    schema: SchemaInfo,
    number: int,
    question: str,
) -> bool:
    """Run one question through every step, printing each. Returns False if a step failed."""
    print(f"[{number}] {question}")
    started = time.perf_counter()
    try:
        draft = generate_sql(llm, schema_text=describe_schema(schema), question=question)
        print("    sql:")
        print(textwrap.indent(draft.sql, " " * 8))
        print(f"    explanation: {draft.explanation}")
        query = validate_sql(
            draft.sql, allowed_tables=schema.table_names, max_rows=settings.max_rows
        )
        result = execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds)
    except UnsafeSqlError as exc:
        print(f"    REJECTED: {exc.reason}\n")
        return False
    except (LLMError, QueryError, ValueError) as exc:
        print(f"    FAILED ({type(exc).__name__}): {exc}\n")
        return False
    print_result(result)
    print(f"    ({llm.name}, total {time.perf_counter() - started:.1f}s)\n")
    return True


def print_result(result: QueryResult) -> None:
    """A small text table: the columns, then the first few rows."""
    print(f"    result: {' | '.join(result.columns)}")
    for row in result.rows[:PREVIEW_ROWS]:
        print(f"            {' | '.join(str(value) for value in row)}")
    more = result.row_count - PREVIEW_ROWS
    note = f"{result.row_count} rows" + (f", {more} not shown" if more > 0 else "")
    if result.truncated:
        note += ", TRUNCATED at the row limit"
    print(f"            ({note}, {result.duration_ms} ms)")


if __name__ == "__main__":
    sys.exit(main())
