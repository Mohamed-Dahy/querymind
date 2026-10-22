"""check_result: each warning sign fires when it should, and stays quiet on normal results."""

from collections.abc import Sequence

import pytest

from querymind.agent.sanity import RETRY_CODES, check_result
from querymind.db.executor import QueryResult


def result(columns: list[str], rows: Sequence[Sequence[object]]) -> QueryResult:
    return QueryResult.model_validate(
        {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "duration_ms": 1,
        }
    )


def codes(columns: list[str], rows: Sequence[Sequence[object]]) -> list[str]:
    return [w.code for w in check_result(result(columns, rows))]


def test_a_normal_result_has_no_warnings() -> None:
    assert codes(["country", "order_count"], [["Germany", 122], ["USA", 122], ["Brazil", 83]]) == []


def test_empty() -> None:
    (warning,) = check_result(result(["n"], []))
    assert warning.code == "empty"
    assert "return the same query" in warning.detail  # an empty answer can be right


def test_duplicate_rows() -> None:
    (warning,) = check_result(result(["city"], [["Berlin"], ["London"], ["Berlin"], ["Berlin"]]))
    assert warning.code == "duplicate_rows"
    assert warning.detail.startswith("2 of the 4 rows")


def test_same_value_in_one_column_is_not_a_duplicate_row() -> None:
    # Germany and USA both have 122: equal numbers are normal, only whole equal rows count.
    assert codes(["country", "n"], [["Germany", 122], ["USA", 122]]) == []


@pytest.mark.parametrize("column", ["total_sales", "revenue", "Quantity", "unit_price", "freight"])
def test_negative_total(column: str) -> None:
    assert codes(["name", column], [["a", 10], ["b", -3.5]]) == ["negative_total"]


@pytest.mark.parametrize("column", ["revenue_change", "growth", "sales_diff", "temperature", "n"])
def test_negative_values_that_can_be_negative(column: str) -> None:
    # A change can go down, and a column that isn't total-like says nothing either way.
    assert codes(["name", column], [["a", 10], ["b", -3.5]]) == []


def test_absurd_magnitude() -> None:
    assert codes(["total"], [[2.5e12]]) == ["absurd_magnitude"]
    assert codes(["total"], [[1_265_793.04]]) == []  # Northwind's total revenue: fine


def test_text_and_booleans_are_not_numbers() -> None:
    # True is an int in Python: it must not count as 1 (or as a negative/huge number).
    assert codes(["total", "flag"], [["-5", True], ["x", False]]) == []


def test_several_warnings_at_once() -> None:
    rows = [["a", -1e13], ["a", -1e13]]
    assert codes(["name", "total"], rows) == [
        "duplicate_rows",
        "negative_total",
        "absurd_magnitude",
    ]


def test_only_empty_and_duplicates_trigger_a_repair() -> None:
    assert set(RETRY_CODES) == {"empty", "duplicate_rows"}
