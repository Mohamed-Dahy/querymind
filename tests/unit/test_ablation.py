"""evals/ablation.py: arm commands, reading the reports, and the table. No LLM, no DB."""

import sys
from pathlib import Path

import pytest

from evals.ablation import (
    STUDIES,
    AblationError,
    Arm,
    Study,
    collect,
    command,
    report_path,
    run,
    table,
)
from evals.report import write_reports
from tests.unit.test_report import outcome

STUDY = Study(
    name="demo",
    title="Demo study",
    suites=("golden", "chinook"),
    provider="gemini",
    headline="match",
    arms=(
        Arm("A", "plain", ("--max-attempts", "1")),
        Arm("B", "+ retries", ("--max-attempts", "3")),
        Arm("C", "+ more", ()),
    ),
)


def save(path: Path, *outcomes: object, llm: str = "gemini / flash-lite") -> None:
    write_reports(path, list(outcomes), title="t", meta={"LLM": llm}, max_rows=1000)  # type: ignore[arg-type]


def test_command_runs_evals_run_with_the_arm_flags_and_a_report(tmp_path: Path) -> None:
    arm = STUDY.arms[0]
    assert command(STUDY, arm, "chinook", tmp_path) == [
        sys.executable,
        *("-m", "evals.run", "--suite", "chinook", "--provider", "gemini"),
        *("--max-attempts", "1"),
        *("--report", str(tmp_path / "phase4-ablation-demo-A-chinook.md")),
    ]


def test_every_arm_adds_one_part_and_bird_c_reuses_the_43_run() -> None:
    golden = STUDIES["golden"]
    assert [arm.key for arm in golden.arms] == ["A", "B", "C", "D"]
    assert "--no-semantic-layer" in golden.arms[1].flags  # B: still no definitions
    assert "--no-semantic-layer" not in golden.arms[2].flags  # C adds them
    bird_c = STUDIES["bird"].arms[2]
    assert report_path(STUDIES["bird"], bird_c, "bird").name == "phase4-bird-100-seq.md"


def test_table_has_both_scores_the_change_and_links(tmp_path: Path) -> None:
    passing = [outcome(f"q{n}", passed=True, ex=True, sql_attempts=1) for n in range(3)]
    save(
        tmp_path / "phase4-ablation-demo-A-golden.md",
        *passing[:1],
        outcome("q9", passed=False, ex=False),
    )
    save(tmp_path / "phase4-ablation-demo-A-chinook.md", *passing[1:2])
    save(
        tmp_path / "phase4-ablation-demo-B-golden.md",
        *passing,
        outcome("q9", passed=True, ex=False, sql_attempts=3),
    )
    save(tmp_path / "phase4-ablation-demo-B-chinook.md")  # an empty suite still counts as run

    text = table(STUDY, collect(STUDY, tmp_path), tmp_path)
    rows = {line.split(" | ")[0]: line for line in text.splitlines() if line.startswith("| ")}
    assert "| 2/3 (66.7%) | 2/3 (66.7%) |  |" in rows["| A"]  # first arm: no change to show
    assert "| 4/4 (100.0%) | 3/4 (75.0%) | +33.3 pts |" in rows["| B"]
    assert "| 0.50 |" in rows["| B"]  # one question took 2 repairs, 3 none: 2 / 4
    assert "[phase4-ablation-demo-A-golden](phase4-ablation-demo-A-golden.md)" in rows["| A"]
    assert "not run yet: golden, chinook" in rows["| C"]
    assert "gemini / flash-lite" in text


def test_arms_on_different_models_are_refused(tmp_path: Path) -> None:
    save(tmp_path / "phase4-ablation-demo-A-golden.md", llm="groq / gpt-oss-120b")
    save(tmp_path / "phase4-ablation-demo-B-golden.md", llm="gemini / flash-lite")
    with pytest.raises(AblationError, match="different models"):
        collect(STUDY, tmp_path)


def test_one_arm_mixing_models_across_suites_is_refused(tmp_path: Path) -> None:
    save(tmp_path / "phase4-ablation-demo-A-golden.md", llm="groq / gpt-oss-120b")
    save(tmp_path / "phase4-ablation-demo-A-chinook.md", llm="gemini / flash-lite")
    with pytest.raises(AblationError, match="arm A mixes models"):
        collect(STUDY, tmp_path)


def test_run_keeps_existing_reports_and_never_reruns_a_reused_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []

    class Done:
        returncode = 0

    def fake_run(cmd: list[str], check: bool) -> Done:
        ran.append(cmd)
        return Done()

    monkeypatch.setattr("evals.ablation.subprocess.run", fake_run)
    save(tmp_path / "phase4-ablation-demo-A-golden.md")
    study = Study(
        "demo", "Demo", ("golden",), "gemini", "ex", (*STUDY.arms[:2], Arm("C", "r", (), "old-run"))
    )

    assert run(study, [], force=False, report_dir=tmp_path) == 0
    assert [cmd[cmd.index("--report") + 1] for cmd in ran] == [
        str(tmp_path / "phase4-ablation-demo-B-golden.md")  # A was kept, C is reused (and missing)
    ]
    assert "MISSING (not re-run)" in capsys.readouterr().out


def test_run_can_redo_one_suite_of_one_arm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[str] = []

    class Done:
        returncode = 0

    def fake_run(cmd: list[str], check: bool) -> Done:
        ran.append(cmd[cmd.index("--suite") + 1])
        return Done()

    monkeypatch.setattr("evals.ablation.subprocess.run", fake_run)
    save(tmp_path / "phase4-ablation-demo-B-chinook.md")  # exists, but --force
    assert run(STUDY, ["B"], force=True, suites=["chinook"], report_dir=tmp_path) == 0
    assert ran == ["chinook"]
