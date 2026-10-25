"""The accuracy gate for CI (4.7): 12 fixed golden questions, compared with a saved baseline.

    uv run python -m evals.gate run                  # what CI runs: ask the 12, then check
    uv run python -m evals.gate check <report.json>...   # check saved reports against the baseline
    uv run python -m evals.gate baseline <report.json>...  # write evals/ci_baseline.json

A prompt or agent change can quietly make answers worse; unit tests can't see that (they use a
fake LLM). So on every PR that touches the agent, CI asks a small fixed set of questions through
the real agent and a real LLM, and fails when **more than one** question that passed in the
baseline now fails. One is allowed: an LLM isn't fully deterministic, even at temperature 0.

Rules (`check`):
- a question the LLM provider never answered (rate limit, outage) is *not scored*: a warning, not
  a failure, as in every eval (an outage says nothing about the code);
- the run must use the baseline's model and agent settings, or the comparison means nothing;
- a question missing from the reports is an error (the CI command and the set drifted apart).
"""

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from evals.models import MODELS, SAME_SETTINGS, command
from evals.report import Outcome, load_report

BASELINE_FILE = Path(__file__).parent / "ci_baseline.json"
REPORT_DIR = Path("ci-reports")
#: Its own Groq quota (each model has one), so CI never spends the development model's day.
CI_MODEL = MODELS["gpt-oss-20b"]
#: Mixed tiers, both businesses; all pass in the baseline, so a failure means something changed.
CI_QUESTIONS = {
    "golden": (
        "e02_discontinued_count",
        "e07_orders_1997",
        "m02_beverages",
        "m05_freight_per_shipper",
        "m10_orders_per_month_1997",
        "h01_top5_products_revenue",
        "h06_running_revenue_1997",
    ),
    "business": ("b04_carrier_most_late", "b11_key_account_turnover"),
    "chinook": ("c02_artist_count", "c07_music_sales_2023", "c13_sales_per_year"),
}
ALLOWED_REGRESSIONS = 1
#: Agent settings that must match the baseline (the suite differs per report, so not checked).
SETTINGS = tuple(key for key in SAME_SETTINGS if key != "Suite")


class GateError(Exception):
    """The reports can't be compared with the baseline (other model, settings or questions)."""


class Baseline(BaseModel):
    llm: str
    settings: dict[str, str]
    passed: list[str]  # the questions that passed when the baseline was made
    failed: list[str]  # ...and those that didn't (a later fail of these isn't a regression)


@dataclass(frozen=True)
class Verdict:
    ok: bool
    regressions: list[tuple[str, str]]  # (question id, why it failed now)
    fixed: list[str]  # failed in the baseline, pass now
    not_scored: list[str]

    def summary(self) -> str:
        lines = [f"**Accuracy gate: {'passed' if self.ok else 'FAILED'}**", ""]
        lines.append(
            f"- regressions (passed in the baseline, fail now): {len(self.regressions)} "
            f"(allowed: {ALLOWED_REGRESSIONS})"
        )
        lines += [f"  - `{qid}`: {why[:200]}" for qid, why in self.regressions]
        if self.fixed:
            lines.append(f"- now passing: {', '.join(self.fixed)}")
        if self.not_scored:
            lines.append(f"- not scored (provider unavailable): {', '.join(self.not_scored)}")
        return "\n".join(lines)


def ci_ids() -> set[str]:
    return {qid for ids in CI_QUESTIONS.values() for qid in ids}


def read_reports(paths: Sequence[Path]) -> tuple[str, dict[str, str], list[Outcome]]:
    """The LLM, agent settings and the CI questions' outcomes of some JSON reports.

    Raises GateError if the reports disagree on the model or settings, or lack a CI question.
    """
    llms, settings, outcomes = set(), set(), []
    for path in paths:
        saved = load_report(path)
        llms.add(saved.meta.get("LLM", "unknown"))
        settings.add(tuple(saved.meta.get(key, "") for key in SETTINGS))
        outcomes += [o for o in saved.outcomes if o.item.id in ci_ids()]
    if len(llms) != 1 or len(settings) != 1:
        raise GateError("the reports were made with different models or agent settings")
    missing = sorted(ci_ids() - {o.item.id for o in outcomes})
    if missing:
        raise GateError(f"the reports lack CI questions: {', '.join(missing)}")
    return llms.pop(), dict(zip(SETTINGS, settings.pop(), strict=True)), outcomes


def make_baseline(paths: Sequence[Path]) -> Baseline:
    llm, settings, outcomes = read_reports(paths)
    unscored = sorted(o.item.id for o in outcomes if not o.scored)
    if unscored:
        raise GateError(f"a baseline needs every question scored; not scored: {unscored}")
    return Baseline(
        llm=llm,
        settings=settings,
        passed=sorted(o.item.id for o in outcomes if o.passed),
        failed=sorted(o.item.id for o in outcomes if not o.passed),
    )


def check(paths: Sequence[Path], baseline: Baseline) -> Verdict:
    """Compare a run with the baseline. Raises GateError if they can't be compared."""
    llm, settings, outcomes = read_reports(paths)
    if llm != baseline.llm:
        raise GateError(f"the run used {llm}, the baseline {baseline.llm}")
    if settings != baseline.settings:
        diff = [k for k in SETTINGS if settings[k] != baseline.settings.get(k)]
        raise GateError(f"agent settings differ from the baseline: {', '.join(diff)}")
    scored = [o for o in outcomes if o.scored]
    regressions = [
        (o.item.id, o.reason or o.error or "wrong answer")
        for o in scored
        if not o.passed and o.item.id in baseline.passed
    ]
    return Verdict(
        ok=len(regressions) <= ALLOWED_REGRESSIONS,
        regressions=regressions,
        fixed=sorted(o.item.id for o in scored if o.passed and o.item.id in baseline.failed),
        not_scored=sorted(o.item.id for o in outcomes if not o.scored),
    )


def run_commands(report_dir: Path = REPORT_DIR) -> list[list[str]]:
    """One evals.run per suite: the full agent on CI_MODEL (evals/models.py), only the CI ids."""
    return [
        [*command(CI_MODEL, suite, report_dir), "--ids", *ids]
        for suite, ids in CI_QUESTIONS.items()
    ]


def report(verdict: Verdict) -> int:
    """Print the verdict (and add it to the GitHub job summary); the exit code for CI."""
    text = verdict.summary()
    print(text)
    for qid in verdict.not_scored:  # a yellow annotation on the PR, not a red cross
        print(f"::warning::{qid} was not scored: the LLM provider was unavailable")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with Path(summary_file).open("a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0 if verdict.ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The CI accuracy gate (4.7).")
    commands = parser.add_subparsers(dest="action", required=True)
    run_parser = commands.add_parser("run", help="ask the CI questions (real LLM), then check")
    run_parser.add_argument("--out", type=Path, default=REPORT_DIR)
    for name in ("check", "baseline"):
        sub = commands.add_parser(name, help=f"{name} saved JSON reports")
        sub.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # Windows console

    try:
        if args.action == "baseline":
            baseline = make_baseline(args.reports)
            BASELINE_FILE.write_text(baseline.model_dump_json(indent=2) + "\n", encoding="utf-8")
            print(
                f"Written {BASELINE_FILE}: {len(baseline.passed)} pass, {len(baseline.failed)} fail"
            )
            return 0
        baseline = Baseline.model_validate(json.loads(BASELINE_FILE.read_text(encoding="utf-8")))
        paths = args.reports if args.action == "check" else []
        if args.action == "run":
            for cmd in run_commands(args.out):
                print(" ".join(cmd[1:]), flush=True)
                if subprocess.run(cmd, check=False).returncode:
                    print("Error: the eval run failed (see above)", file=sys.stderr)
                    return 1
            paths = [
                args.out / f"phase4-models-{CI_MODEL.key}-{suite}.json" for suite in CI_QUESTIONS
            ]
        return report(check(paths, baseline))
    except (GateError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
