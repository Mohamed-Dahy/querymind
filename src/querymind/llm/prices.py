"""List prices of the models we know, and what a run's tokens cost (4.6, 5.8b).

    prices = load_prices()                                         # llm/prices.yaml, read once
    cost_usd(prices, "groq", "openai/gpt-oss-20b", 1200, 300)      # -> 0.00018, or None

Prices are US dollars per 1 million tokens, paid tier, each with its source and the date they
were checked. Evals use them for a "list-price equivalent" (our runs are on free tiers); the API
for the cost shown with every answer. A model that isn't listed has no cost (None), never a
guess: prices change, and a wrong number is worse than none. More models come in Phase 9.
"""

from datetime import date
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

PRICES_FILE = Path(__file__).parent / "prices.yaml"


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0)  # dollars per 1M input tokens
    output: float = Field(ge=0)  # dollars per 1M output tokens (thinking included)
    source: str
    note: str = ""


class Prices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked: date  # YAML reads 2026-10-25 as a date
    models: dict[str, Price]  # "provider / model", as eval reports record their LLM


def load_prices(path: Path = PRICES_FILE) -> Prices:
    """The price list in `path` (read each time: evals and tests pass their own)."""
    return Prices.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


@cache
def known_prices() -> Prices:
    """The package's price list, read once per process (the API prices every answer)."""
    return load_prices()


def cost_usd(
    prices: Prices,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> float | None:
    """What these tokens cost at list price; None when the model isn't listed or the tokens
    aren't known. Rounded to a millionth of a dollar (a question costs fractions of a cent)."""
    price = prices.models.get(f"{provider} / {model}")
    if price is None or input_tokens is None or output_tokens is None:
        return None
    dollars = (input_tokens * price.input + output_tokens * price.output) / 1_000_000
    return round(dollars, 6)
