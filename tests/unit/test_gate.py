"""evals/gate.py: the CI accuracy gate's rules, with saved fake reports. No LLM, no DB."""

import json
from collections.abc import Collection
from pathlib import Path

import pytest

from evals.ablation import STUDIES
from evals.gate import (
    ALLOWED_REGRESSIONS,
    BASELINE_FILE,
    CI_MODEL,
    CI_QUESTIONS,
    Baseline,
    GateError,
    Verdict,
    check,
    ci_ids,
    make_baseline,
    report,
    run_commands,
)
from evals.report import Outcome, write_reports
from tests.unit.test_report import outcome

SETTINGS = {
    "Max SQL attempts": "3",
    "Semantic layer": "northwind.yaml, chinook.yaml",
    "Schema retrieval": "auto (hybrid, top-k 5, max 8 tables)",
    "Few-shot examples": "3",
}
ALL = sorted(ci_ids())
BASELINE = Baseline(llm=CI_MODEL.llm, settings=SETTINGS, passed=ALL[1:], failed=ALL[:1])


def save(tmp_path: Path, outcomes: list[Outcome], *, llm: str = CI_MODEL.llm, **meta: str) -> Path:
    path = tmp_path / "run.md"
    write_reports(path, outcomes, title="t", meta={"LLM": llm, **SETTINGS, **meta}, max_rows=1000)
    return path.with_suffix(".json")


def results(failing: Collection[str] = (), not_scored: Collection[str] = ()) -> list[Outcome]:
    return [
        outcome(qid, passed=qid not in failing, ex=qid not in failing, scored=qid not in not_scored)
        for qid in ALL
    ]


def test_ci_runs_the_full_agent_on_its_model_and_only_its_questions(tmp_path: Path) -> None:
    commands = run_commands(tmp_path)
    assert [cmd[cmd.index("--suite") + 1] for cmd in commands] == list(CI_QUESTIONS)
    for cmd, ids in zip(commands, CI_QUESTIONS.values(), strict=True):
        assert cmd[cmd.index("--model") + 1] == CI_MODEL.name
        assert all(flag in cmd for flag in STUDIES["golden"].arms[-1].flags)  # arm D
        assert cmd[cmd.index("--ids") + 1 :] == list(ids)
    assert len(ci_ids()) == 12


def test_the_saved_baseline_matches_the_ci_questions_and_model() -> None:
    baseline = Baseline.model_validate(json.loads(BASELINE_FILE.read_text(encoding="utf-8")))
    assert set(baseline.passed) | set(baseline.failed) == ci_ids()
    assert baseline.llm == CI_MODEL.llm


def test_no_change_passes_and_a_fix_is_reported(tmp_path: Path) -> None:
    verdict = check([save(tmp_path, results())], BASELINE)
    assert verdict.ok and verdict.regressions == []
    assert verdict.fixed == ALL[:1]  # failed in the baseline, passes now


def test_one_regression_is_allowed_two_fail_the_build(tmp_path: Path) -> None:
    assert ALLOWED_REGRESSIONS == 1
    one = check([save(tmp_path, results(failing={ALL[1]}))], BASELINE)
    assert one.ok and [qid for qid, _ in one.regressions] == [ALL[1]]
    two = check([save(tmp_path, results(failing={ALL[1], ALL[2]}))], BASELINE)
    assert not two.ok


def test_a_question_that_failed_in_the_baseline_too_is_no_regression(tmp_path: Path) -> None:
    verdict = check([save(tmp_path, results(failing={ALL[0]}))], BASELINE)
    assert verdict.ok and verdict.regressions == []


def test_not_scored_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    # A rate limit says nothing about the code: those questions leave the comparison.
    verdict = check([save(tmp_path, results(not_scored={ALL[1], ALL[2], ALL[3]}))], BASELINE)
    assert verdict.ok and verdict.not_scored == ALL[1:4]


def test_another_model_or_settings_or_missing_questions_are_refused(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="the baseline groq"):
        check([save(tmp_path, results(), llm="gemini / flash-lite")], BASELINE)
    with pytest.raises(GateError, match="Few-shot examples"):
        check([save(tmp_path, results(), **{"Few-shot examples": "off"})], BASELINE)
    with pytest.raises(GateError, match="lack CI questions"):
        check([save(tmp_path, results()[1:])], BASELINE)


def test_a_baseline_needs_every_question_scored(tmp_path: Path) -> None:
    with pytest.raises(GateError, match="not scored"):
        make_baseline([save(tmp_path, results(not_scored={ALL[0]}))])
    baseline = make_baseline([save(tmp_path, results(failing={ALL[0]}))])
    assert baseline.failed == ALL[:1] and baseline.passed == ALL[1:]


def test_report_sets_the_exit_code_and_writes_the_job_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    failed = Verdict(
        ok=False, regressions=[("q1", "wrong"), ("q2", "wrong")], fixed=[], not_scored=["q3"]
    )
    assert report(failed) == 1
    assert "**Accuracy gate: FAILED**" in summary.read_text(encoding="utf-8")
    assert "::warning::q3 was not scored" in capsys.readouterr().out
    assert report(Verdict(ok=True, regressions=[], fixed=[], not_scored=[])) == 0
