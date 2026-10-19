"""Smoke test for the LLM interface: one text call and one structured call to a real provider.

    uv run python scripts/llm_smoke.py                       # provider + model from .env
    uv run python scripts/llm_smoke.py --provider gemini     # the other provider
    uv run python scripts/llm_smoke.py --provider groq --model openai/gpt-oss-20b

Costs a few tokens. Unit tests never do this; they use FakeLLMProvider.
"""

import argparse
import sys
import time

from pydantic import BaseModel, Field

from querymind.config import ConfigError, get_settings
from querymind.llm import LLMError, create_llm_provider

SCHEMA = """\
customers(customer_id, company_name, city, country)
orders(order_id, customer_id, order_date, ship_country)
order_details(order_id, product_id, unit_price, quantity, discount)"""


class SqlDraft(BaseModel):
    """A taste of Phase 1.2: the shape we'll ask the LLM to fill."""

    sql: str = Field(description="One PostgreSQL SELECT statement")
    explanation: str = Field(description="One sentence on what the query does")
    tables_used: list[str] = Field(description="Tables the query reads")


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the configured LLM provider once.")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="override LLM_PROVIDER")
    parser.add_argument(
        "--model", help="override that provider's model (GROQ_MODEL / GEMINI_MODEL)"
    )
    args = parser.parse_args()

    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        if args.model:
            settings = settings.model_copy(update={f"{settings.llm_provider}_model": args.model})
        model, _ = settings.llm_credentials()
    except ConfigError as exc:
        print(exc)
        return 1
    llm = create_llm_provider(settings)
    print(f"Provider: {llm.name} | model: {model}\n")

    try:
        started = time.perf_counter()
        text = llm.generate_text("In one short sentence: what does a SQL JOIN do?")
        print(f"[text] {time.perf_counter() - started:.1f}s\n{text.strip()}\n")

        started = time.perf_counter()
        draft = llm.generate_structured(
            f"Database tables:\n{SCHEMA}\n\nQuestion: How many orders did each country place?",
            SqlDraft,
            system="You write correct PostgreSQL for the given tables.",
        )
        print(f"[structured] {time.perf_counter() - started:.1f}s -> {type(draft).__name__}")
        print(f"sql:         {draft.sql}")
        print(f"explanation: {draft.explanation}")
        print(f"tables_used: {draft.tables_used}")
    except LLMError as exc:
        print(f"FAILED ({type(exc).__name__}): {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
