"""Ablations (4.4): what does each part of QueryMind add? Run the arms, then build the table.

    uv run python -m evals.ablation run golden            # arms A-D on the 58 golden questions
    uv run python -m evals.ablation run bird --arms A B   # only some arms
    uv run python -m evals.ablation run golden --arms D --suites chinook --force   # redo one report
    uv run python -m evals.ablation table                 # docs/eval-reports/phase4-ablations.md

An **ablation** switches parts off to measure what each one is worth. Each arm here adds ONE part to
the arm before it, so the difference between two rows is what that part adds. Rules:
- one model for every arm of a study (`provider`), checked when the table is built;
- arms run one question at a time (no --workers), so the latency column is the agent's time, not
  time spent waiting for the rate limiter (4.3);
- every number in the table links to the report it came from.

The arms are written down here, in code, so the whole table can be run again with one command.
"""

import argparse
import statistics
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from evals.report import Outcome, SavedReport, load_report, summarize_outcomes

REPORT_DIR = Path("docs/eval-reports")
# Settings that make an arm "plain": no retries, no definitions, the whole schema, no examples.
_OFF = ("--no-semantic-layer", "--schema-retrieval", "off", "--few-shot", "off")


class AblationError(Exception):
    """The reports can't make a fair table (e.g. two arms used different models)."""


@dataclass(frozen=True)
class Arm:
    key: str  # "A", "B"... and part of the report file names
    adds: str  # what this arm adds to the one before it
    flags: tuple[str, ...]  # passed to evals.run
    #: A report that already has exactly this arm's settings (no need to spend the tokens again).
    reuse: str | None = None


@dataclass(frozen=True)
class Study:
    name: str
    title: str
    suites: tuple[str, ...]  # evals.run --suite values; an arm's score is over all of them
    provider: str
    headline: str  # "match" (our own sets) or "ex" (BIRD: comparable with the leaderboard)
    arms: tuple[Arm, ...]


STUDIES = {
    "golden": Study(
        name="golden",
        title="Golden 58: Northwind 30 + business terms 13 + Chinook 15",
        suites=("golden", "business", "chinook"),
        provider="gemini",
        headline="match",
        arms=(
            Arm(
                "A",
                "plain LLM: one try, whole schema, nothing else",
                ("--max-attempts", "1", *_OFF),
            ),
            Arm(
                "B", "+ self-correction and sanity check (3 tries)", ("--max-attempts", "3", *_OFF)
            ),
            Arm(
                "C",
                "+ semantic layer (business definitions)",
                ("--max-attempts", "3", "--schema-retrieval", "off", "--few-shot", "off"),
            ),
            Arm(
                "D",
                "+ schema retrieval and few-shot examples (the full agent)",
                ("--max-attempts", "3", "--schema-retrieval", "auto", "--few-shot", "on"),
            ),
        ),
    ),
    "bird": Study(
        name="bird",
        title="BIRD-100: the fixed stratified subset, 11 databases",
        suites=("bird",),
        provider="gemini",
        headline="ex",
        arms=(
            Arm("A", "plain LLM: one try, no hints", ("--max-attempts", "1", "--no-evidence")),
            Arm("B", "+ BIRD's evidence hints", ("--max-attempts", "1")),
            # The 4.3 sequential run: evidence on, 3 tries, Gemini flash-lite, one at a time.
            Arm(
                "C",
                "+ self-correction (the full agent)",
                ("--max-attempts", "3"),
                "phase4-bird-100-seq",
            ),
        ),
    ),
}


def report_path(study: Study, arm: Arm, suite: str, report_dir: Path = REPORT_DIR) -> Path:
    """Where an arm's report for one suite lives (Markdown; the JSON is next to it)."""
    if arm.reuse:
        return report_dir / f"{arm.reuse}.md"
    return report_dir / f"phase4-ablation-{study.name}-{arm.key}-{suite}.md"


def command(study: Study, arm: Arm, suite: str, report_dir: Path = REPORT_DIR) -> list[str]:
    """The evals.run command for one arm on one suite."""
    path = report_path(study, arm, suite, report_dir)
    return [
        sys.executable,
        "-m",
        "evals.run",
        "--suite",
        suite,
        "--provider",
        study.provider,
        *arm.flags,
        "--report",
        str(path),
    ]


def run(
    study: Study,
    keys: Sequence[str],
    *,
    force: bool,
    suites: Sequence[str] = (),
    report_dir: Path = REPORT_DIR,
) -> int:
    """Run the chosen arms (all if `keys` is empty), suite by suite (all if `suites` is empty).

    Reports that exist are kept unless `force`.
    """
    for arm in study.arms:
        if keys and arm.key not in keys:
            continue
        for suite in study.suites:
            if suites and suite not in suites:
                continue
            path = report_path(study, arm, suite, report_dir)
            if arm.reuse:  # never overwritten: it belongs to an earlier step
                found = "kept" if path.with_suffix(".json").exists() else "MISSING (not re-run)"
                print(f"Arm {arm.key} / {suite}: {found} {path}")
                continue
            if path.with_suffix(".json").exists() and not force:
                print(f"Arm {arm.key} / {suite}: kept {path}")
                continue
            print(
                f"\nArm {arm.key} / {suite}: {' '.join(command(study, arm, suite, report_dir)[1:])}"
            )
            exit_code = subprocess.run(
                command(study, arm, suite, report_dir), check=False
            ).returncode
            if exit_code:
                print(
                    f"Arm {arm.key} / {suite} failed (exit {exit_code}): stopping", file=sys.stderr
                )
                return exit_code
    return 0


@dataclass(frozen=True)
class ArmResult:
    """One row of the table: an arm's numbers over all its suites."""

    arm: Arm
    reports: list[Path]  # the Markdown reports the numbers come from
    missing: list[str]  # suites without a report yet
    outcomes: list[Outcome]
    llm: str | None

    @property
    def scored(self) -> list[Outcome]:
        return [o for o in self.outcomes if o.scored]


def collect(study: Study, report_dir: Path = REPORT_DIR) -> list[ArmResult]:
    """Read every arm's reports. Raises AblationError if the arms don't share one model."""
    results = []
    for arm in study.arms:
        reports, missing, outcomes, models = [], [], [], set()
        for suite in study.suites:
            path = report_path(study, arm, suite, report_dir)
            if not path.with_suffix(".json").exists():
                missing.append(suite)
                continue
            saved: SavedReport = load_report(path.with_suffix(".json"))
            reports.append(path)
            outcomes.extend(saved.outcomes)
            models.add(saved.meta.get("LLM", "unknown"))
        if len(models) > 1:
            raise AblationError(f"arm {arm.key} mixes models: {', '.join(sorted(models))}")
        results.append(ArmResult(arm, reports, missing, outcomes, next(iter(models), None)))
    models = {r.llm for r in results if r.llm}
    if len(models) > 1:
        raise AblationError(f"{study.name}: arms use different models: {', '.join(sorted(models))}")
    return results


def table(study: Study, results: Sequence[ArmResult], report_dir: Path = REPORT_DIR) -> str:
    """The Markdown table for one study: one row per arm, the change against the row above."""
    llm = next((r.llm for r in results if r.llm), "not run yet")
    head = "BIRD EX" if study.headline == "ex" else "QueryMind match"
    lines = [
        f"## {study.title}",
        "",
        f"- **LLM (every arm):** {llm} · **headline:** {head} · one question at a time",
        "",
        "| Arm | Adds | QueryMind match | BIRD EX | Δ headline | Not scored | Repairs / q "
        "| Tokens / q (in + out) | Median s / q | Reports |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    previous: float | None = None
    for result in results:
        links = " · ".join(
            f"[{p.stem}]({p.relative_to(report_dir).as_posix()})" for p in result.reports
        )
        if result.missing:
            links += f" (not run yet: {', '.join(result.missing)})"
        if not result.scored:
            lines.append(f"| {result.arm.key} | {result.arm.adds} | | | | | | | | {links} |")
            continue
        summary = summarize_outcomes(result.outcomes)
        score = summary.ex if study.headline == "ex" else summary.accuracy
        delta = "" if previous is None else f"{score.percent - previous:+.1f} pts"
        previous = score.percent
        repairs = statistics.mean(max(o.sql_attempts - 1, 0) for o in result.scored)
        median = statistics.median(o.seconds for o in result.scored)
        lines.append(
            f"| {result.arm.key} | {result.arm.adds} | {summary.accuracy} | {summary.ex} | {delta} "
            f"| {len(summary.not_scored)} | {repairs:.2f} "
            f"| {summary.input_tokens:,.0f} + {summary.output_tokens:,.0f} "
            f"| {median:.1f} | {links} |"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run ablation arms and build their table (4.4).")
    commands = parser.add_subparsers(dest="action", required=True)
    run_parser = commands.add_parser("run", help="run a study's arms (real LLM calls)")
    run_parser.add_argument("study", choices=STUDIES)
    run_parser.add_argument("--arms", nargs="+", default=[], help="only these arms, e.g. A B")
    run_parser.add_argument("--suites", nargs="+", default=[], help="only these suites")
    run_parser.add_argument("--force", action="store_true", help="run again even if reports exist")
    table_parser = commands.add_parser("table", help="build the table from the saved reports")
    table_parser.add_argument("--out", type=Path, default=REPORT_DIR / "phase4-ablations.md")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # Windows console

    if args.action == "run":
        return run(STUDIES[args.study], args.arms, force=args.force, suites=args.suites)
    try:
        sections = [table(study, collect(study)) for study in STUDIES.values()]
    except (AblationError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    text = "# Phase 4.4: ablations, what each part adds\n\n" + "\n\n".join(sections) + "\n"
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
