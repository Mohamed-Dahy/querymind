"""Ask QueryMind questions from the terminal: the full Phase 1 pipeline, step by step.

    uv run python scripts/ask.py "How many orders did each country place?"
    uv run python scripts/ask.py                     # the 10 sample questions below
    uv run python scripts/ask.py --provider gemini   # the other provider

question → SQL (LLM) → validator → executor (as analyst_ro) → plain-language answer (LLM, grounded).
"""

import argparse
import sys
import textwrap

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest
from querymind.config import ConfigError, get_settings
from querymind.db.errors import QueryError
from querymind.llm import LLMError, create_llm_provider
from querymind.safety.errors import UnsafeSqlError

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
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)

    failures = 0
    for number, question in enumerate(args.questions or SAMPLE_QUESTIONS, start=1):
        print(f"[{number}] {question}")
        try:
            print_reply(pipeline.ask(question))
        except UnsafeSqlError as exc:
            failures += 1
            print(f"    REJECTED: {exc.reason}\n")
        except (LLMError, QueryError, ValueError) as exc:
            failures += 1
            print(f"    FAILED ({type(exc).__name__}): {exc}\n")
    return 1 if failures else 0


def print_reply(reply: AgentReply) -> None:
    """An answer, a clarifying question with its options, or a refusal."""
    if isinstance(reply, AskResult):
        print_result(reply)
    elif isinstance(reply, ClarificationRequest):
        print(f"    ASKS BACK: {reply.clarification.question}")
        for option in reply.clarification.options:
            print(f"      - {option}")
        print()
    else:
        print(f"    REFUSES: {reply.message}")
        print()


def print_result(result: AskResult) -> None:
    """The answer first (what a user wants), then the SQL and the first rows as evidence."""
    source = "" if result.answer.from_llm else "  [fallback: the LLM answer was not used]"
    print(f"    answer: {result.answer.text}{source}")
    for warning in result.warnings:
        print(f"    WARNING ({warning.code}): {warning.message}")
    for number, attempt in enumerate(result.failed_attempts, start=1):
        print(f"    try {number} {attempt.error_kind}: {attempt.detail[:110]}")
    tries = f" (after {result.sql_attempts} tries)" if result.sql_attempts > 1 else ""
    print(f"    sql{tries}:")
    print(textwrap.indent(result.sql, " " * 8))
    data = result.result
    print(f"    result: {' | '.join(data.columns)}")
    for row in data.rows[:PREVIEW_ROWS]:
        print(f"            {' | '.join(str(value) for value in row)}")
    more = data.row_count - PREVIEW_ROWS
    note = f"{data.row_count} rows" + (f", {more} not shown" if more > 0 else "")
    if data.truncated:
        note += ", TRUNCATED at the row limit"
    print(f"            ({note}, query {data.duration_ms} ms)\n")


if __name__ == "__main__":
    sys.exit(main())
