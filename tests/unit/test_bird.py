"""The BIRD loader, the stratified subset and the question text. No downloads, no database."""

import json
from collections import Counter
from pathlib import Path

import pytest

from evals.bird import (
    BirdError,
    BirdQuestion,
    interleave,
    load_bird_questions,
    load_subset,
    orders_rows,
    question_text,
    save_subset,
    stratified_subset,
    to_golden,
)


def bird(
    qid: int, *, db: str = "formula_1", difficulty: str = "simple", **extra: str
) -> dict[str, object]:
    return {
        "question_id": qid,
        "db_id": db,
        "question": f"Question {qid}?",
        "evidence": extra.get("evidence", f"hint {qid}"),
        "SQL": extra.get("sql", "SELECT 1"),
        "difficulty": difficulty,
    }


def questions(rows: list[dict[str, object]]) -> list[BirdQuestion]:
    return [BirdQuestion.model_validate(row) for row in rows]


# 60 questions: 3 databases x (6 simple, 10 moderate, 4 challenging) = 30% / 50% / 20%.
MIX = [
    bird(db_number * 100 + i, db=db, difficulty=difficulty)
    for db_number, db in enumerate(["card_games", "financial", "superhero"], start=1)
    for i, difficulty in enumerate(["simple"] * 6 + ["moderate"] * 10 + ["challenging"] * 4)
]


def test_loads_the_bird_file(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps([bird(1471, db="debit_card_specializing")]), encoding="utf-8")
    [q] = load_bird_questions(path)
    assert (q.id, q.database, q.sql) == ("bird_1471", "bird_debit_card_specializing", "SELECT 1")


def test_missing_file_says_how_to_get_it(tmp_path: Path) -> None:
    with pytest.raises(BirdError, match=r"seed_bird.py"):
        load_bird_questions(tmp_path / "nope.json")


@pytest.mark.parametrize(
    "row",
    [
        {**bird(1), "difficulty": "impossible"},  # not one of BIRD's three levels
        {**bird(1), "db_id": "Formula 1; DROP"},  # becomes part of a database name
        {"question_id": 1},  # fields missing
    ],
)
def test_rejects_files_that_are_not_bird(tmp_path: Path, row: dict[str, object]) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps([row]), encoding="utf-8")
    with pytest.raises(BirdError, match="not a BIRD questions file"):
        load_bird_questions(path)


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    path = tmp_path / "q.json"
    path.write_text(json.dumps([bird(1), bird(1)]), encoding="utf-8")
    with pytest.raises(BirdError, match="duplicate"):
        load_bird_questions(path)


def test_subset_keeps_the_mix_of_difficulties_and_databases() -> None:
    subset = stratified_subset(questions(MIX), size=20, seed=7)
    assert len(subset) == 20
    assert Counter(q.difficulty for q in subset) == {"simple": 6, "moderate": 10, "challenging": 4}
    # Each database has a third of every difficulty: 2 simple, 3-4 moderate, 1-2 challenging.
    by_db = Counter(q.db_id for q in subset)
    assert set(by_db) == {"card_games", "financial", "superhero"}
    assert all(6 <= n <= 8 for n in by_db.values())
    assert len({q.question_id for q in subset}) == 20  # no question twice


def test_subset_is_the_same_for_the_same_seed() -> None:
    first = stratified_subset(questions(MIX), size=20, seed=7)
    again = stratified_subset(list(reversed(questions(MIX))), size=20, seed=7)  # order-proof
    other = stratified_subset(questions(MIX), size=20, seed=8)
    assert [q.question_id for q in first] == [q.question_id for q in again]
    assert [q.question_id for q in first] != [q.question_id for q in other]


def test_subset_size_adds_up_after_rounding() -> None:
    # 7 of 60 can't be split evenly: largest-remainder rounding still gives exactly 7.
    assert len(stratified_subset(questions(MIX), size=7, seed=1)) == 7


def test_subset_size_must_fit() -> None:
    with pytest.raises(ValueError, match="between 1 and 60"):
        stratified_subset(questions(MIX), size=61, seed=1)


def test_subset_file_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "subset.yaml"
    chosen = stratified_subset(questions(MIX), size=10, seed=3)
    save_subset(chosen, seed=3, path=path)
    assert load_subset(questions(MIX), path) == chosen
    assert path.read_text(encoding="utf-8").startswith("# The fixed BIRD")


def test_subset_ids_must_exist(tmp_path: Path) -> None:
    path = tmp_path / "subset.yaml"
    save_subset(questions([bird(999)]), seed=3, path=path)
    with pytest.raises(BirdError, match="999"):
        load_subset(questions(MIX), path)


def test_question_with_and_without_the_hint() -> None:
    [q] = questions([bird(5, evidence="  rate = a / b ")])
    assert question_text(q, evidence=True) == "Question 5?\nHint: rate = a / b"
    assert question_text(q, evidence=False) == "Question 5?"
    [blank] = questions([bird(6, evidence=" ")])
    assert question_text(blank, evidence=True) == "Question 6?"  # no empty "Hint:"


def test_to_golden_maps_difficulty_to_tier() -> None:
    [q] = questions([bird(9, difficulty="challenging", sql="SELECT a FROM t ORDER BY a")])
    item = to_golden(q, evidence=False)
    assert (item.id, item.tier, item.question, item.ordered) == (
        "bird_9",
        "hard",
        "Question 9?",
        True,
    )


@pytest.mark.parametrize(
    ("sql", "ordered"),
    [
        ("SELECT name FROM drivers ORDER BY points DESC", True),
        ("SELECT name FROM drivers ORDER BY points DESC LIMIT 3", True),
        ("SELECT name FROM drivers ORDER BY points DESC LIMIT 1", False),  # one row
        ("SELECT name FROM drivers", False),
        ("SELECT n FROM (SELECT n FROM t ORDER BY n) AS s", False),  # inner ORDER BY only
        ("not sql at all (", False),
    ],
)
def test_orders_rows(sql: str, ordered: bool) -> None:
    assert orders_rows(sql) is ordered


def test_interleave_spreads_a_short_run_over_every_database() -> None:
    mixed = questions([bird(1, db="b"), bird(2, db="b"), bird(3, db="b"), bird(4, db="a")])
    assert [q.question_id for q in interleave(mixed)] == [4, 1, 2, 3]  # a1, b1, b2, b3
    assert [q.db_id for q in interleave(questions(MIX))[:3]] == [
        "card_games",
        "financial",
        "superhero",
    ]
