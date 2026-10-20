"""Check that every number in an LLM answer really comes from the query result.

LLMs can "hallucinate" numbers: the result says 156 orders, the answer says 165. This check
pulls every number out of the answer and looks for it in the data. A number with no match is
"ungrounded", and the summarizer then falls back to a safe, templated answer.

Allowed sources: every value in the result rows (numbers, and numbers written inside text such
as dates), the column names, the row count, and the question itself ("top 5", "in 1997").

Matching is as precise as the answer: "1,265,793" must be within 0.5 of a value, "1.27 million"
within 5,000 (half of its last digit), "12.5%" matches 12.5 or 0.125. So honest rounding passes
and changed numbers don't.

Limits: it checks numbers, not words ("Beverages sold the least" isn't caught), and numbers
inside dates count as known, so a small number like 4 passes if any date has day 04.

Regex is fine here: we are *checking* free text the LLM wrote, not reading data our code relies on
(that always comes back as a validated Pydantic object, see CLAUDE.md §9).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import JsonValue

from querymind.db.executor import QueryResult

# A number as people write it: 156 · 1,265,793 · 1265793.04 · 12.5% · 1.27 million · $3.2M
_NUMBER = re.compile(
    r"(?<![\w.])"  # not inside a word or code ("Q4", "v2") or right after a decimal point
    r"(\d{1,3}(?:,\d{3})+|\d+)"  # integer part, with or without thousands commas
    r"(?:\.(\d+))?"  # optional decimals
    r"(?!\d)"
    r"(?:\s*(%|percent\b|thousand\b|million\b|billion\b|k\b|m\b|bn\b))?",  # optional unit
    re.IGNORECASE,
)

_SCALES = {"thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}


@dataclass(frozen=True)
class NumberMention:
    """One number found in a text."""

    text: str  # as written, e.g. "1.27 million"
    value: float  # e.g. 1270000.0
    tolerance: float  # half of the last written digit, e.g. 5000.0
    is_percent: bool


def find_numbers(text: str) -> list[NumberMention]:
    """Every number in `text`, with the precision it was written at."""
    mentions = []
    for match in _NUMBER.finditer(text):
        integer, decimals, unit = match.groups()
        unit = (unit or "").lower()
        scale = _SCALES.get(unit, 1.0)
        places = len(decimals) if decimals else 0
        value = float(integer.replace(",", "") + (f".{decimals}" if decimals else ""))
        mentions.append(
            NumberMention(
                text=match.group(0).strip(),
                value=value * scale,
                # "156" means 155.5-156.5; "1.27 million" means 1,265,000-1,275,000.
                tolerance=0.5 * 10**-places * scale,
                is_percent=unit in ("%", "percent"),
            )
        )
    return mentions


def find_ungrounded_numbers(answer: str, *, result: QueryResult, question: str = "") -> list[str]:
    """Numbers in `answer` that match nothing in the result (or the question). Empty = grounded."""
    known = _known_values(result, question)
    return [
        mention.text
        for mention in find_numbers(answer)
        if not any(_matches(mention, value) for value in known)
    ]


def _known_values(result: QueryResult, question: str) -> set[float]:
    """Every number the answer may use. Signs are dropped: "fell by 4%" may describe -4."""
    values: set[float] = {float(result.row_count)}
    # Underscores become spaces so "orders_1997" offers 1997 (otherwise it reads as one word).
    texts = [question, *(column.replace("_", " ") for column in result.columns)]
    for row in result.rows:
        for cell in row:
            values.update(_numbers_in_cell(cell, texts))
    for text in texts:
        values.update(mention.value for mention in find_numbers(text))
    return values


def _numbers_in_cell(cell: JsonValue, texts: list[str]) -> Iterable[float]:
    """Numeric cells directly; text cells (e.g. dates '1997-07-04') are collected into `texts`."""
    if isinstance(cell, bool) or cell is None:  # bool first: True is an int in Python
        return []
    if isinstance(cell, int | float):
        return [abs(float(cell))]
    if isinstance(cell, str):
        texts.append(cell)
        return []
    items = cell if isinstance(cell, list) else list(cell.values())
    return [value for item in items for value in _numbers_in_cell(item, texts)]


def _matches(mention: NumberMention, known: float) -> bool:
    # A tiny relative margin absorbs float noise (0.1 + 0.2 != 0.3).
    margin = mention.tolerance + 1e-9 * max(1.0, known)
    if abs(mention.value - known) <= margin:
        return True
    # "12.5%" may describe a stored ratio of 0.125.
    return mention.is_percent and abs(mention.value - known * 100) <= margin
