"""evals/rescore.py on the real Northwind: saved SQL is re-run and scored again, no LLM."""

from dataclasses import replace
from pathlib import Path

import pytest

from evals.golden_set import GoldenQuestion
from evals.report import Outcome, SavedReport, write_reports
from evals.rescore import changes, main, rescore
from querymind.config import Settings

pytestmark = pytest.mark.integration

COUNT = GoldenQuestion(
    id="q_count", tier="easy", question="How many customers?", sql="SELECT count(*) FROM customers"
)


def answered(sql: str, *, passed: bool, ex: bool, **extra: object) -> Outcome:
    return Outcome(
        item=COUNT,
        passed=passed,
        strict=passed,
        reason="",
        error=None,
        sql=sql,
        seconds=1.0,
        provider_attempts=1,
        sql_attempts=1,
        confidence="high",
        ex=ex,
        **extra,  # type: ignore[arg-type]
    )


def saved(*outcomes: Outcome) -> SavedReport:
    return SavedReport(
        meta={}, outcomes=list(outcomes), reference_failures={}, tier_names={}, max_rows=1000
    )


def test_rescore_reruns_both_sqls_and_applies_todays_rules(settings: Settings) -> None:
    right = answered("SELECT count(*) AS n FROM customers", passed=False, ex=False)  # stale
    wrong = answered("SELECT count(*) FROM orders", passed=True, ex=True)  # stale the other way
    # Our match ignores the extra column, BIRD EX doesn't.
    extra = answered("SELECT count(*), 1 FROM customers", passed=False, ex=False)
    asked_back = answered("SELECT 1", passed=False, ex=False, scored=False)  # never answered
    errored = replace(right, error="QueryExecutionError")  # failed in the run: keeps its verdict

    after = rescore(saved(right, wrong, extra, asked_back, errored), settings)
    assert [(o.passed, o.ex) for o in after] == [
        (True, True),
        (False, False),
        (True, False),
        (False, False),
        (False, False),
    ]
    assert "official EX compares whole rows" in after[2].ex_reason
    assert changes([right, wrong], after[:2]) == [
        "q_count: match FAIL → PASS, EX FAIL → PASS",
        "q_count: match PASS → FAIL, EX PASS → FAIL",
    ]


def test_rescore_command_reproduces_a_saved_run(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = answered("SELECT count(*) FROM customers", passed=True, ex=True)
    json_path = write_reports(tmp_path / "run.md", [good], title="t", meta={}, max_rows=1000)
    assert main([str(json_path), "--report", str(tmp_path / "again.md")]) == 0
    printed = capsys.readouterr().out
    assert "QueryMind match: 1/1 (100.0%) → 1/1 (100.0%)" in printed
    assert "Changed verdicts: none" in printed
    assert (tmp_path / "again.json").exists()
