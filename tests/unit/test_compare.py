"""compare_results: the same answer passes however it's written; a different answer never does."""

from collections.abc import Sequence

import pytest

from evals.compare import bird_ex, compare_results, values_equal
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


COUNTRIES = result(["country", "count"], [["Germany", 122], ["USA", 122], ["Brazil", 83]])


def same(actual: QueryResult, *, expected: QueryResult = COUNTRIES, ordered: bool = False) -> bool:
    return compare_results(expected=expected, actual=actual, ordered=ordered).match


# --- The same answer, written differently: must match ----------------------------------------


def test_identical() -> None:
    assert same(COUNTRIES)


def test_column_names_do_not_matter() -> None:
    assert same(result(["c", "order_count"], COUNTRIES.rows))


def test_column_order_does_not_matter() -> None:
    assert same(result(["n", "country"], [[122, "Germany"], [122, "USA"], [83, "Brazil"]]))


def test_row_order_does_not_matter_when_unordered() -> None:
    assert same(result(["a", "b"], [["Brazil", 83], ["USA", 122], ["Germany", 122]]))


def test_extra_columns_match_but_are_not_strict() -> None:
    extra = result(
        ["id", "country", "count"], [[1, "Germany", 122], [2, "USA", 122], [3, "Brazil", 83]]
    )
    comparison = compare_results(expected=COUNTRIES, actual=extra, ordered=False)
    assert comparison.match is True
    assert comparison.strict is False


def test_exact_columns_are_strict() -> None:
    assert compare_results(expected=COUNTRIES, actual=COUNTRIES, ordered=False).strict is True


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (617085.2023927002, 617085.20),  # float4 sum vs ROUND(..., 2)
        (617085.2023927002, 617085.2025),  # float4 vs numeric arithmetic
        (21.162083500729633, 21.16),  # a percentage rounded to 2 decimals
        (1.0, 1),  # extract(month) gives 1.0, date_part may give 1
        (1997, "1997"),  # to_char(...) gives text
        (" Germany ", "Germany"),
        (None, None),
    ],
)
def test_equal_values(expected: object, actual: object) -> None:
    assert values_equal(expected, actual)  # type: ignore[arg-type]


# --- A different answer: must not match ------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "actual"),
    [
        (617085.20, 617483.39),  # revenue without the discount
        (21.16, 0.2116),  # a ratio where a percentage (0-100) was asked
        (21.16, 21.2),  # rounded too far
        (122, 123),
        ("Germany", "germany"),  # different text is different
        (None, 0),  # NULL is not zero
        (True, 1),  # a boolean is not a number
        ("1997-01-01", 1997),
    ],
)
def test_different_values(expected: object, actual: object) -> None:
    assert not values_equal(expected, actual)  # type: ignore[arg-type]


def test_missing_row() -> None:
    comparison = compare_results(
        expected=COUNTRIES, actual=result(["a", "b"], COUNTRIES.rows[:2]), ordered=False
    )
    assert comparison.match is False
    assert comparison.reason == "expected 3 rows, got 2"


def test_missing_column() -> None:
    comparison = compare_results(
        expected=COUNTRIES, actual=result(["country"], [["Germany"], ["USA"], ["Brazil"]]),
        ordered=False,
    )  # fmt: skip
    assert comparison.reason == "expected 2 columns, got 1"


def test_wrong_value_in_one_column() -> None:
    wrong = result(["a", "b"], [["Germany", 122], ["USA", 122], ["Brazil", 84]])
    comparison = compare_results(expected=COUNTRIES, actual=wrong, ordered=False)
    assert comparison.match is False
    assert comparison.reason == "no column matches the values of 'count'"


def test_columns_match_alone_but_rows_are_paired_wrongly() -> None:
    # Same names and same counts, but Brazil got Germany's number.
    swapped = result(["a", "b"], [["Germany", 83], ["USA", 122], ["Brazil", 122]])
    comparison = compare_results(expected=COUNTRIES, actual=swapped, ordered=False)
    assert comparison.match is False
    assert comparison.reason == "the columns match one by one, but the rows don't line up"


# --- Ordered questions (rankings, time series) ----------------------------------------------

TOP3 = result(
    ["company", "revenue"],
    [["QUICK-Stop", 110277.31], ["Ernst Handel", 104874.98], ["Save-a-lot", 104361.95]],
)


def test_ordered_same_order_matches() -> None:
    assert same(TOP3, expected=TOP3, ordered=True)


def test_ordered_wrong_order_fails() -> None:
    reordered = result(["company", "revenue"], [TOP3.rows[1], TOP3.rows[0], TOP3.rows[2]])
    assert not same(reordered, expected=TOP3, ordered=True)
    assert same(reordered, expected=TOP3, ordered=False)  # same rows, so unordered passes


def test_ordered_with_columns_swapped_still_matches() -> None:
    swapped = result(["revenue", "company"], [[row[1], row[0]] for row in TOP3.rows])
    assert same(swapped, expected=TOP3, ordered=True)


# --- Edge cases ------------------------------------------------------------------------------


def test_two_columns_with_identical_values() -> None:
    # Both columns hold the same values: either pairing must be tried.
    expected = result(["a", "b"], [[1, 1], [2, 2]])
    actual = result(["x", "y"], [[1, 1], [2, 2]])
    assert same(actual, expected=expected)


def test_empty_results_match_each_other() -> None:
    assert same(result(["a"], []), expected=result(["count"], []))


def test_nulls_sort_and_match() -> None:
    expected = result(["first", "manager"], [["Andrew", None], ["Nancy", "Andrew"]])
    actual = result(["f", "m"], [["Nancy", "Andrew"], ["Andrew", None]])
    assert same(actual, expected=expected)


# --- BIRD's official EX vs our match: where they agree and where they don't (4.2) -------------

GOLD = result(["name", "points"], [["Hamilton", 413], ["Vettel", 317]])


def official(actual: QueryResult, *, expected: QueryResult = GOLD) -> bool:
    return bird_ex(expected=expected, actual=actual).match


def rows(*values: list[object]) -> QueryResult:
    """A result with GOLD's two columns."""
    return result(GOLD.columns, values)


def wide(*values: list[object]) -> QueryResult:
    """A result with a third column."""
    return result(["name", "points", "wins"], values)


@pytest.mark.parametrize(
    ("case", "actual", "ours", "bird"),
    [
        ("identical", GOLD, True, True),
        ("other names", result(["driver", "pts"], GOLD.rows), True, True),
        ("other row order", rows(["Vettel", 317], ["Hamilton", 413]), True, True),
        # A set has no duplicates: BIRD ignores them, we count rows.
        ("duplicate row", rows(["Hamilton", 413], ["Vettel", 317], ["Vettel", 317]), False, True),
        # A row is a tuple: BIRD compares position by position.
        ("swapped columns", rows([413, "Hamilton"], [317, "Vettel"]), True, False),
        ("extra column", wide(["Hamilton", 413, 11], ["Vettel", 317, 5]), True, False),
        # Exact values for BIRD; tolerance and number-as-text for us.
        ("rounding noise", rows(["Hamilton", 413.0000001], ["Vettel", 317]), True, False),
        ("number as text", rows(["Hamilton", "413"], ["Vettel", "317"]), True, False),
        ("python: 413 == 413.0", rows(["Hamilton", 413.0], ["Vettel", 317.0]), True, True),
        ("missing row", rows(["Hamilton", 413]), False, False),
        ("wrong value", rows(["Hamilton", 400], ["Vettel", 317]), False, False),
    ],
)
def test_official_ex_and_our_match(case: str, actual: QueryResult, ours: bool, bird: bool) -> None:
    assert same(actual, expected=GOLD) is ours, case
    assert official(actual) is bird, case


def test_official_ex_handles_nulls_arrays_and_json() -> None:
    gold = result(["a", "b"], [[None, [1, 2]], [{"k": 1, "j": [2]}, None]])
    again = result(["x", "y"], [[{"j": [2], "k": 1}, None], [None, [1, 2]]])  # dict key order
    assert official(again, expected=gold)
    assert not official(result(["x", "y"], [[None, [2, 1]]]), expected=gold)  # array order counts


def test_official_ex_empty_results() -> None:
    empty = result(["name"], [])
    assert official(empty, expected=empty)
    assert not official(empty)
    assert "2 expected rows missing" in bird_ex(expected=GOLD, actual=empty).reason


def test_official_ex_says_why() -> None:
    assert "expected 2 columns, got 3" in bird_ex(expected=GOLD, actual=wide(["H", 1, 1])).reason
    off = rows(["Hamilton", 400], ["Vettel", 317])
    assert bird_ex(expected=GOLD, actual=off).reason == (
        "official EX: 1 expected rows missing, 1 unexpected rows"
    )


def test_official_ex_refuses_a_cut_off_result() -> None:
    cut = QueryResult(columns=["n"], rows=[[1]], row_count=1, truncated=True, duration_ms=1)
    assert "row cap" in bird_ex(expected=cut, actual=cut).reason
