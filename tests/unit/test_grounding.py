"""Grounding: every number in an answer must come from the result (or the question)."""

import pytest

from querymind.agent.grounding import find_numbers, find_ungrounded_numbers
from querymind.db.executor import QueryResult


def make_result(columns: list[str], rows: list[list[object]]) -> QueryResult:
    return QueryResult.model_validate(
        {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
            "duration_ms": 1,
        }
    )


SALES = make_result(
    ["category_name", "total_sales", "share", "first_order"],
    [
        ["Beverages", 267868.18, 0.212, "1996-07-04"],
        ["Dairy Products", 234507.28, 0.186, "1996-07-05"],
        ["Confections", 167357.22, 0.133, "1996-07-08"],
    ],
)


def ungrounded(answer: str, question: str = "Sales per category?") -> list[str]:
    return find_ungrounded_numbers(answer, result=SALES, question=question)


# --- find_numbers --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value", "tolerance"),
    [
        ("156", 156, 0.5),
        ("1,265,793", 1265793, 0.5),
        ("1265793.04", 1265793.04, 0.005),
        ("$3,000", 3000, 0.5),
        ("12.5%", 12.5, 0.05),
        ("1.27 million", 1_270_000, 5_000),
        ("$3.2M", 3_200_000, 50_000),
        ("4.5k", 4_500, 50),
        ("2 billion", 2e9, 0.5e9),
    ],
)
def test_reads_numbers_with_their_precision(text: str, value: float, tolerance: float) -> None:
    [mention] = find_numbers(f"about {text} in total")
    assert mention.value == pytest.approx(value)
    assert mention.tolerance == pytest.approx(tolerance)


def test_ignores_numbers_inside_words() -> None:
    assert find_numbers("Q4 results for model v2 and SKU A1") == []


def test_finds_every_number() -> None:
    assert [m.text for m in find_numbers("5 of 77 products, 6.5% of stock")] == ["5", "77", "6.5%"]


# --- find_ungrounded_numbers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "answer",
    [
        "Beverages sold the most: 267,868.18.",  # exact, with thousands commas
        "Beverages led with about 267,868 in sales.",  # rounded to whole
        "Beverages led with roughly 268 thousand.",  # rounded with a unit
        "Beverages led with $0.27 million.",
        "Beverages had a 21.2% share.",  # a stored ratio (0.212) written as a percent
        "Three categories are shown; Beverages is first with 0.212 of sales.",
        "There are 3 categories.",  # the row count
        "The first order was on 1996-07-04.",  # numbers inside text cells (dates)
        "Since 1996, Beverages has led.",
        "Beverages is the top category.",  # no numbers at all
    ],
)
def test_grounded_answers_pass(answer: str) -> None:
    assert ungrounded(answer) == []


@pytest.mark.parametrize(
    ("answer", "invented"),
    [
        ("Beverages sold 276,868.18.", ["276,868.18"]),  # digits swapped
        ("Beverages sold about 300,000.", ["300,000"]),  # "rounded" too far
        ("Beverages sold 0.4 million.", ["0.4 million"]),  # 350k-450k: 267,868 isn't in it
        ("Together they sold 669,732.68.", ["669,732.68"]),  # a sum the LLM computed itself
        ("Beverages had a 25% share.", ["25%"]),
        ("There are 9 categories.", ["9"]),  # not the row count (3), nor in any date
        ("Beverages sold 267,868.18, up 12% on last year.", ["12%"]),  # partly invented
    ],
)
def test_invented_numbers_are_caught(answer: str, invented: list[str]) -> None:
    assert ungrounded(answer) == invented


def test_numbers_from_the_question_are_allowed() -> None:
    assert ungrounded("Here are the top 5 categories for 1997.", "Top 5 categories in 1997?") == []


def test_honest_rounding_at_low_precision_passes() -> None:
    # 267,868 really is "0.3 million" to one decimal: precision follows how the answer is written.
    assert ungrounded("Beverages sold about 0.3 million.") == []


def test_known_limit_small_numbers_inside_dates_count() -> None:
    # 1996-07-04 contains 4, so "4" passes even though it means nothing here. Documented limit.
    assert ungrounded("There are 4 categories.") == []


def test_numbers_in_column_names_are_allowed() -> None:
    result = make_result(["orders_1997"], [[408]])
    assert find_ungrounded_numbers("In 1997 there were 408 orders.", result=result) == []


def test_negative_values_can_be_described_without_the_sign() -> None:
    result = make_result(["change"], [[-4.0]])
    assert find_ungrounded_numbers("Sales fell by 4%.", result=result) == []


def test_booleans_are_not_numbers() -> None:
    result = make_result(["discontinued"], [[True]])
    assert find_ungrounded_numbers("1 product is discontinued.", result=result) == []  # row count
    assert find_ungrounded_numbers("2 products are discontinued.", result=result) == ["2"]
