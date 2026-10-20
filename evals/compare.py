"""Compare two query results the way a person would: by their values, not their SQL or names.

    comparison = compare_results(expected=reference, actual=agent, ordered=False)
    comparison.match    # same answer?
    comparison.strict   # ...and no extra columns?
    comparison.reason   # why not, in plain words

What counts as the same answer:
  - column NAMES never matter (`count` vs `order_count`), and neither does column ORDER;
  - extra columns in the actual result are allowed (`strict` tells you if there were any);
  - row order matters only when `ordered=True` (rankings, time series);
  - numbers match within a small tolerance: float4-vs-numeric noise and ROUND(x, 2) pass;
  - a number written as text ('1997') equals the number 1997.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import permutations

from pydantic import JsonValue

from querymind.db.executor import QueryResult

# Two numbers are equal if they differ by at most this much (absolute)...
ABS_TOLERANCE = 0.005  # ...so a value rounded to 2 decimals still matches
# ...or by this fraction of their size (for big sums: float4 noise grows with the value).
REL_TOLERANCE = 1e-6

type Value = JsonValue
type Row = Sequence[Value]


@dataclass(frozen=True)
class Comparison:
    match: bool
    strict: bool  # match AND the same number of columns (no extras)
    reason: str  # "" when they match


def compare_results(*, expected: QueryResult, actual: QueryResult, ordered: bool) -> Comparison:
    """Does `actual` give the same answer as `expected`?"""
    if actual.row_count != expected.row_count:
        return _no(f"expected {expected.row_count} rows, got {actual.row_count}")
    width = len(expected.columns)
    if len(actual.columns) < width:
        return _no(f"expected {width} columns, got {len(actual.columns)}")

    expected_columns = _columns(expected.rows, width)
    actual_columns = _columns(actual.rows, len(actual.columns))

    # For each expected column: which actual columns hold the same values? Comparing whole
    # columns first prunes the search below to a handful of combinations.
    candidates: list[list[int]] = []
    for index, column in enumerate(expected_columns):
        found = [
            i for i, other in enumerate(actual_columns) if _same_values(column, other, ordered)
        ]
        if not found:
            return _no(f"no column matches the values of {expected.columns[index]!r}")
        candidates.append(found)

    # Try each way of pairing expected columns with distinct actual columns, and check the
    # full rows line up (columns can match one by one but pair up differently across rows).
    for mapping in _mappings(candidates):
        picked = [[row[i] for i in mapping] for row in actual.rows]
        if _same_rows(expected.rows, picked, ordered):
            return Comparison(match=True, strict=len(actual.columns) == width, reason="")
    return _no("the columns match one by one, but the rows don't line up")


def values_equal(expected: Value, actual: Value) -> bool:
    """One value against another, with number tolerance and number-as-text."""
    expected_number, actual_number = _as_number(expected), _as_number(actual)
    if expected_number is not None and actual_number is not None:
        difference = abs(expected_number - actual_number)
        scale = max(abs(expected_number), abs(actual_number))
        return difference <= ABS_TOLERANCE or difference <= REL_TOLERANCE * scale
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip() == actual.strip()
    if isinstance(expected, bool) or isinstance(actual, bool):
        # In Python True == 1, but a yes/no flag is not the number 1.
        return type(expected) is type(actual) and expected == actual
    return expected == actual


def _no(reason: str) -> Comparison:
    return Comparison(match=False, strict=False, reason=reason)


def _columns(rows: Sequence[Row], width: int) -> list[list[Value]]:
    return [[row[i] for row in rows] for i in range(width)]


def _same_values(expected: list[Value], actual: list[Value], ordered: bool) -> bool:
    if not ordered:
        expected, actual = sorted(expected, key=_sort_key), sorted(actual, key=_sort_key)
    return all(values_equal(e, a) for e, a in zip(expected, actual, strict=True))


def _same_rows(expected: Sequence[Row], actual: Sequence[Row], ordered: bool) -> bool:
    if not ordered:
        # Sort both the same way so equal rows line up; then compare pair by pair.
        expected = sorted(expected, key=lambda row: [_sort_key(v) for v in row])
        actual = sorted(actual, key=lambda row: [_sort_key(v) for v in row])
    return all(
        all(values_equal(e, a) for e, a in zip(expected_row, actual_row, strict=True))
        for expected_row, actual_row in zip(expected, actual, strict=True)
    )


def _mappings(candidates: list[list[int]]) -> list[tuple[int, ...]]:
    """Every assignment of a distinct actual column to each expected column."""
    pool = sorted({i for found in candidates for i in found})
    return [
        mapping
        for mapping in permutations(pool, len(candidates))
        if all(i in found for i, found in zip(mapping, candidates, strict=True))
    ]


def _sort_key(value: Value) -> tuple[int, float, str]:
    """A total order over mixed values: NULLs first, then numbers, then text.

    Numbers are rounded to the tolerance, so 263.5 and 263.499999 sort to the same place.
    """
    if value is None:
        return (0, 0.0, "")
    number = _as_number(value)
    if number is not None:
        return (1, round(number, 2), "")
    return (2, 0.0, str(value).strip())


def _as_number(value: Value) -> float | None:
    if isinstance(value, bool):  # True is an int in Python, but not a number here
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
