"""The golden set file is well-formed, and the loader rejects broken files. No database."""

from pathlib import Path

import pytest

from evals.golden_set import GOLDEN_DIR, GoldenSetError, load_golden_set

NORTHWIND = GOLDEN_DIR / "northwind.yaml"
BUSINESS = GOLDEN_DIR / "northwind_business.yaml"
CHINOOK = GOLDEN_DIR / "chinook.yaml"


def test_northwind_has_30_questions_across_tiers() -> None:
    golden = load_golden_set(NORTHWIND)
    assert golden.database == "northwind"
    assert len(golden.questions) == 30
    counts = {tier: len(golden.by_tier(tier)) for tier in ("easy", "medium", "hard")}
    assert counts == {"easy": 10, "medium": 12, "hard": 8}


def test_northwind_covers_every_skill() -> None:
    tags = {tag for q in load_golden_set(NORTHWIND).questions for tag in q.tags}
    assert tags >= {"filter", "join", "group_by", "date_range", "top_n", "window", "cte"}


def test_business_questions_all_need_a_definition() -> None:
    business = load_golden_set(BUSINESS)
    assert business.database == "northwind"
    assert len(business.questions) == 13
    assert all("business_term" in q.tags for q in business.questions)
    # The two files are scored separately, but their ids must not collide in reports.
    golden_ids = {q.id for q in load_golden_set(NORTHWIND).questions}
    assert golden_ids.isdisjoint(q.id for q in business.questions)


def test_chinook_has_15_questions_across_tiers() -> None:
    chinook = load_golden_set(CHINOOK)
    assert chinook.database == "chinook"
    counts = {tier: len(chinook.by_tier(tier)) for tier in ("easy", "medium", "hard")}
    assert counts == {"easy": 5, "medium": 6, "hard": 4}
    ids = [q.id for path in (NORTHWIND, BUSINESS, CHINOOK) for q in load_golden_set(path).questions]
    assert len(ids) == len(set(ids))  # unique across every suite


def test_ranking_and_time_series_questions_are_ordered() -> None:
    golden = load_golden_set(NORTHWIND)
    ordered = {q.id for q in golden.questions if q.ordered}
    assert ordered == {
        "e06_top5_expensive",
        "m10_orders_per_month_1997",
        "h01_top5_products_revenue",
        "h02_top3_customers_revenue",
        "h04_revenue_per_year",
        "h06_running_revenue_1997",
    }


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "golden.yaml"
    path.write_text(text, encoding="utf-8")
    return path


GOOD_ENTRY = """
  - id: q1
    tier: easy
    question: How many?
    sql: SELECT 1
"""


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("database: x\nversion: 1\nquestions:" + GOOD_ENTRY * 2, "duplicate question ids: q1"),
        ("database: x\nversion: 1\nquestions:" + GOOD_ENTRY + "    orderd: true\n", "orderd"),
        ("database: x\nversion: 1\nquestions:" + GOOD_ENTRY.replace("easy", "trivial"), "tier"),
        ("database: x\nversion: 1\nquestions:" + GOOD_ENTRY + "    tags: [joins]\n", "tags"),
        ("database: x\nversion: 1\nquestions:" + GOOD_ENTRY.replace("q1", "Q 1"), "id"),
        ("database: x\nversion: 1\nquestions: []\n", "questions"),
        ("database: x\nquestions: [unclosed\n", "invalid YAML"),
    ],
)
def test_broken_files_are_rejected_clearly(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(GoldenSetError, match=problem):
        load_golden_set(write(tmp_path, text))


def test_yaml_cannot_run_code(tmp_path: Path) -> None:
    # With yaml.load (unsafe), this tag would call os.system. safe_load refuses it.
    text = "database: !!python/object/apply:os.system ['echo hacked']\nversion: 1\n"
    with pytest.raises(GoldenSetError, match="invalid YAML"):
        load_golden_set(write(tmp_path, text))


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(GoldenSetError, match="cannot read file"):
        load_golden_set(tmp_path / "nope.yaml")
