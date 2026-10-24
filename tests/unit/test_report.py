"""evals/report.py: summaries with both scores, breakdowns, and the JSON twin of the report."""

import json
from pathlib import Path

import pytest

from evals.golden_set import GoldenQuestion
from evals.report import (
    Outcome,
    build_report,
    load_report,
    outcome_from_json,
    outcome_to_json,
    summarize_outcomes,
    write_reports,
)


def outcome(
    qid: str,
    *,
    tier: str = "easy",
    passed: bool,
    ex: bool,
    database: str = "bird_a",
    **extra: object,
) -> Outcome:
    item = GoldenQuestion.model_validate(
        {"id": qid, "tier": tier, "question": f"{qid}?", "sql": "SELECT 1", "ordered": True}
    )
    values: dict[str, object] = {
        "passed": passed,
        "strict": passed,
        "reason": "" if passed else "wrong",
        "error": None,
        "sql": "SELECT 2",
        "seconds": 1.0,
        "provider_attempts": 1,
        "sql_attempts": 1,
        "confidence": "high",
        "ex": ex,
        "database": database,
        "llm_calls": 2,
        "input_tokens": 1000,
        "output_tokens": 100,
    }
    return Outcome(item=item, **{**values, **extra})  # type: ignore[arg-type]


OUTCOMES = [
    outcome("q1", passed=True, ex=True),
    outcome("q2", tier="medium", passed=True, ex=False, database="bird_b"),  # e.g. extra column
    outcome("q3", tier="medium", passed=False, ex=True, database="bird_b"),  # e.g. duplicates
    outcome("q4", tier="hard", passed=False, ex=False),
    outcome("q5", passed=False, ex=False, scored=False, input_tokens=0, output_tokens=0),
]


def test_both_scores_leave_out_unscored_questions() -> None:
    summary = summarize_outcomes(OUTCOMES)
    assert (str(summary.accuracy), str(summary.ex)) == ("2/4 (50.0%)", "2/4 (50.0%)")
    assert summary.not_scored == ["q5"]


def test_breakdowns_by_tier_and_database() -> None:
    summary = summarize_outcomes(OUTCOMES)
    assert {t: (str(m), str(e)) for t, (m, e) in summary.by_tier.items()} == {
        "easy": ("1/1 (100.0%)", "1/1 (100.0%)"),
        "medium": ("1/2 (50.0%)", "1/2 (50.0%)"),
        "hard": ("0/1 (0.0%)", "0/1 (0.0%)"),
    }
    assert {db: (m.passed, e.passed) for db, (m, e) in summary.by_database.items()} == {
        "bird_a": (1, 1),
        "bird_b": (1, 1),
    }


def test_agreement_counts_each_verdict_pair() -> None:
    assert summarize_outcomes(OUTCOMES).agreement == {
        "both pass": 1,
        "only QueryMind match": 1,
        "only BIRD EX": 1,
        "both fail": 1,
    }


def test_token_means_are_per_scored_question() -> None:
    summary = summarize_outcomes(OUTCOMES)
    assert (summary.llm_calls, summary.input_tokens, summary.output_tokens) == (2.0, 1000, 100)
    assert summary.total_tokens == 4 * 1100


def test_report_has_both_scores_tiers_names_and_databases() -> None:
    names = {"easy": "simple", "medium": "moderate", "hard": "challenging"}
    report = build_report(OUTCOMES, title="t", meta={}, tier_names=names)
    assert "| **BIRD EX** (official: same set of rows, exactly) | **2/4 (50.0%)** |" in report
    assert "| moderate | 1/2 (50.0%) | 1/2 (50.0%) |" in report
    assert "## By database" in report and "| bird_b |" in report
    assert "| only BIRD EX | 1 |" in report
    assert "| Tokens per question (mean in / out) | 1,000 / 100 |" in report
    assert "## By skill (tag)" not in report  # no tags: no empty table
    # Failing either score is listed in detail, with both verdicts.
    assert "### `q2` (moderate): wrong result (match PASS, BIRD EX FAIL)" in report
    assert "### `q1`" not in report


def test_one_database_has_no_database_table() -> None:
    report = build_report(OUTCOMES[:1], title="t", meta={})
    assert "## By database" not in report


def test_outcome_json_round_trip() -> None:
    for original in OUTCOMES:
        record = json.loads(json.dumps(outcome_to_json(original)))  # through real JSON text
        assert outcome_from_json(record) == original


def test_write_reports_writes_markdown_and_json(tmp_path: Path) -> None:
    path = tmp_path / "run.md"
    json_path = write_reports(
        path,
        OUTCOMES,
        title="Run",
        meta={"LLM": "fake"},
        max_rows=50_000,
        reference_failures={"q9": "rejected"},
        tier_names={"easy": "simple"},
    )
    assert json_path == tmp_path / "run.json"
    assert path.read_text(encoding="utf-8").startswith("# Run\n")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["summary"]["bird_ex"] == [2, 4]
    assert data["outcomes"][1]["sql"] == "SELECT 2"  # every question keeps its SQL

    saved = load_report(json_path)
    assert saved.outcomes == OUTCOMES
    assert (saved.meta, saved.max_rows) == ({"LLM": "fake"}, 50_000)
    assert (saved.reference_failures, saved.tier_names) == ({"q9": "rejected"}, {"easy": "simple"})


def test_load_report_rejects_other_files(tmp_path: Path) -> None:
    path = tmp_path / "other.json"
    path.write_text('{"format": 99}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a QueryMind eval report"):
        load_report(path)
