"""Does a result look wrong even though the query ran? (Phase 2.3)

    warnings = check_result(result)
    [w.code for w in warnings]   # e.g. ["duplicate_rows"]

A query that runs isn't always a right query. These checks catch the common signs of a wrong
one, without knowing the answer:

- `empty`: no rows. Often a filter value spelled differently from the data ('germany').
- `duplicate_rows`: identical rows. Usually a join that matched more rows than intended.
- `negative_total`: a negative number in a total-like column (revenue, quantity, price...).
- `absurd_magnitude`: a number of a trillion or more: far beyond any value in this kind of
  database, typically sums multiplied by a bad join.

The first two get one repair (`RETRY_CODES`): the LLM sees the warning and can fix the query,
or return the same one if the result is truly right. The others are only shown to the user.
They are hints, not proof: an empty result can be the right answer.
"""

import re
from collections import Counter
from collections.abc import Sequence

from pydantic import JsonValue

from querymind.agent.schemas import SanityWarning
from querymind.db.executor import QueryResult

RETRY_CODES = frozenset({"empty", "duplicate_rows"})

ABSURD_MAGNITUDE = 1e12
# A column is "total-like" if a word of its name is one of these...
TOTAL_WORDS = frozenset(
    {"total", "sum", "revenue", "sales", "count", "quantity", "qty", "price", "amount", "freight"}
)
# ...unless another word says it measures a change, which can be negative.
CHANGE_WORDS = frozenset({"change", "diff", "difference", "growth", "delta", "variance", "net"})


def check_result(result: QueryResult) -> list[SanityWarning]:
    """Every sign that this result might be wrong (empty list = it looks fine)."""
    if result.row_count == 0:
        return [
            SanityWarning(
                code="empty",
                message="No rows matched. If you expected results, check the names, "
                "spellings or dates in the question.",
                detail="The query ran but returned no rows. Check each filter value against "
                "the sample rows (spelling, case, codes, date format). If no rows is truly the "
                "right answer, return the same query.",
            )
        ]
    warnings = []
    if duplicates := _duplicate_rows(result.rows):
        warnings.append(
            SanityWarning(
                code="duplicate_rows",
                message="Some rows appear more than once, which can mean a join counted "
                "things twice.",
                detail=f"{duplicates} of the {len(result.rows)} rows are exact duplicates of "
                "another row. This usually means a join multiplied rows: fix the join, or use "
                "DISTINCT. If duplicate rows are expected, return the same query.",
            )
        )
    for index, column in enumerate(result.columns):
        values = [_number(row[index]) for row in result.rows]
        numbers = [v for v in values if v is not None]
        if _is_total_like(column) and any(n < 0 for n in numbers):
            warnings.append(
                SanityWarning(
                    code="negative_total",
                    message=f"'{column}' has negative values, which is unusual for a total.",
                    detail=f"Column {column} has negative values.",
                )
            )
        if any(abs(n) >= ABSURD_MAGNITUDE for n in numbers):
            warnings.append(
                SanityWarning(
                    code="absurd_magnitude",
                    message=f"'{column}' has extremely large values (a trillion or more), "
                    "which may mean a join multiplied rows.",
                    detail=f"Column {column} has values of 1e12 or more.",
                )
            )
    return warnings


def _duplicate_rows(rows: Sequence[Sequence[JsonValue]]) -> int:
    """How many rows repeat an earlier row exactly."""
    counts = Counter(repr(row) for row in rows)  # repr: rows are lists, which can't be hashed
    return sum(count - 1 for count in counts.values())


def _is_total_like(column: str) -> bool:
    words = set(re.split(r"[^a-z]+", column.lower()))
    return bool(words & TOTAL_WORDS) and not words & CHANGE_WORDS


def _number(value: JsonValue) -> float | None:
    # bool is an int in Python, but True is not the number 1 here.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)
