"""Model comparison (4.6): the same agent on several LLMs. Accuracy, latency, tokens and cost.

    uv run python -m evals.models run gpt-oss-20b  # the golden 58 (3 suites)
    uv run python -m evals.models run qwen3.8-27b --suites golden  # one suite (over days)
    uv run python -m evals.models table          # docs/eval-reports/phase4-models.md

Every model runs the full agent with the same flags (4.4 arm D, written down in evals/ablation.py)
and differs only in `--provider` / `--model`. A fair table needs more than that, so `table` also:
- refuses reports whose LLM isn't the model of their row, or whose agent settings or question
  set differ (the 3.1 lesson: `.env` once switched a run to another model);
- scores every model on the same questions: those that *every* model in the study scored (a
  question one model lost to a rate limit is left out for all, and the count says so);
- prices tokens at list price (evals/prices.yaml, paid tier) although we run on free tiers.

Latency is end to end on free tiers (Groq and Google run different hardware), one question at
a time; the Groq runs are spaced out to stay under 8K tokens a minute, so no question waits
inside a rate-limit retry. Cost covers the SQL path the eval runs (no summary, no embeddings:
the embeddings are the same for every model).
"""

import argparse
import math
import statistics
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from evals.ablation import STUDIES
from evals.report import Outcome, SavedReport, load_report

REPORT_DIR = Path("docs/eval-reports")
PRICES_FILE = Path(__file__).parent / "prices.yaml"
#: The full agent, exactly as in the ablation's last arm (D), so the flash-lite row can reuse it.
AGENT_FLAGS = STUDIES["golden"].arms[-1].flags
#: Report meta that must be the same in every row (one agent; only the LLM changes).
SAME_SETTINGS = ("Suite", "Max SQL attempts", "Semantic layer", "Schema retrieval",
                 "Few-shot examples")  # fmt: skip
STUDY_SUITES = {"golden": ("golden", "business", "chinook"), "bird": ("bird",)}


class ModelsError(Exception):
    """The reports can't make a fair comparison (wrong model, different settings...)."""


@dataclass(frozen=True)
class Model:
    key: str  # short name for reports and the command line
    provider: str
    name: str  # the provider's model id
    #: Seconds between questions: Groq's free tier allows 8K tokens a minute, ~4K a question.
    delay: float = 30.0
    #: suite → an existing report with exactly these settings (no need to spend the tokens).
    reuse: dict[str, str] = field(default_factory=dict)

    @property
    def llm(self) -> str:
        """As eval reports record it, e.g. "groq / openai/gpt-oss-20b"."""
        return f"{self.provider} / {self.name}"


MODELS = {
    m.key: m
    for m in (
        Model("gpt-oss-120b", "groq", "openai/gpt-oss-120b"),
        Model("gpt-oss-20b", "groq", "openai/gpt-oss-20b"),
        Model("qwen3.8-27b", "groq", "qwen/qwen3.8-27b"),
        Model(
            "flash-lite",
            "gemini",
            "gemini-3.5-flash-lite",
            delay=1.0,
            reuse={
                **{s: f"phase4-ablation-golden-D-{s}" for s in STUDY_SUITES["golden"]},
                "bird": "phase4-bird-100-seq",  # 4.3: same agent, one at a time
            },
        ),
    )
}


class Price(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: float = Field(ge=0)  # dollars per 1M input tokens
    output: float = Field(ge=0)  # dollars per 1M output tokens (thinking included)
    source: str
    note: str = ""


class Prices(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checked: date  # YAML reads 2026-10-25 as a date
    models: dict[str, Price]


def load_prices(path: Path = PRICES_FILE) -> Prices:
    return Prices.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def report_path(model: Model, suite: str, report_dir: Path = REPORT_DIR) -> Path:
    if suite in model.reuse:
        return report_dir / f"{model.reuse[suite]}.md"
    return report_dir / f"phase4-models-{model.key}-{suite}.md"


def command(model: Model, suite: str, report_dir: Path = REPORT_DIR) -> list[str]:
    """The evals.run command for one model on one suite."""
    return [
        sys.executable, "-m", "evals.run", "--suite", suite,
        "--provider", model.provider, "--model", model.name, *AGENT_FLAGS,
        "--delay", f"{model.delay:g}", "--report", str(report_path(model, suite, report_dir)),
    ]  # fmt: skip


def run(model: Model, suites: Sequence[str], *, force: bool, report_dir: Path = REPORT_DIR) -> int:
    """Run `model` on each suite. Saved reports are kept unless `force`; reused ones always."""
    for suite in suites:
        path = report_path(model, suite, report_dir)
        if suite in model.reuse:
            found = "kept" if path.with_suffix(".json").exists() else "MISSING (not re-run)"
            print(f"{model.key} / {suite}: {found} {path}")
            continue
        if path.with_suffix(".json").exists() and not force:
            print(f"{model.key} / {suite}: kept {path}")
            continue
        print(f"\n{model.key} / {suite}: {' '.join(command(model, suite, report_dir)[1:])}")
        exit_code = subprocess.run(command(model, suite, report_dir), check=False).returncode
        if exit_code:
            print(f"{model.key} / {suite} failed (exit {exit_code}): stopping", file=sys.stderr)
            return exit_code
    return 0


@dataclass(frozen=True)
class ModelRun:
    """One model's reports for one study (all its suites), or why it has none."""

    model: Model
    reports: list[Path]
    outcomes: list[Outcome]
    missing: list[str]  # suites not run yet: the row is left out of the comparison


def collect(
    study: str,
    report_dir: Path = REPORT_DIR,
    models: Sequence[Model] = tuple(MODELS.values()),
    suites: Sequence[str] = (),
) -> list[ModelRun]:
    """Read every model's reports for `study` (only `suites`, if given), checking fairness.

    Raises ModelsError if a report's LLM isn't its row's model, or if two models ran with
    different agent settings or questions.
    """
    runs = []
    settings: dict[str, tuple[str, dict[str, str]]] = {}  # suite → (first model, its meta)
    for model in models:
        reports, outcomes, missing = [], [], []
        for suite in suites or STUDY_SUITES[study]:
            path = report_path(model, suite, report_dir)
            if not path.with_suffix(".json").exists():
                missing.append(suite)
                continue
            saved: SavedReport = load_report(path.with_suffix(".json"))
            llm = saved.meta.get("LLM")
            if llm != model.llm:
                raise ModelsError(f"{path.name}: LLM is {llm}, the row is {model.llm}")
            meta = {key: saved.meta.get(key, "") for key in SAME_SETTINGS}
            first, expected = settings.setdefault(suite, (model.key, meta))
            if meta != expected:
                diff = [k for k in SAME_SETTINGS if meta[k] != expected[k]]
                raise ModelsError(
                    f"{model.key} / {suite} ran with other settings than {first}: {', '.join(diff)}"
                )
            reports.append(path)
            outcomes.extend(saved.outcomes)
        runs.append(ModelRun(model, reports, outcomes, missing))
    return runs


def common_ids(runs: Sequence[ModelRun]) -> set[str]:
    """The questions every complete model scored: each row is scored on exactly these."""
    complete = [r for r in runs if not r.missing]
    if not complete:
        return set()
    return set.intersection(*({o.item.id for o in r.outcomes if o.scored} for r in complete))


def percentile(values: Sequence[float], share: float) -> float:
    """Nearest-rank percentile: the smallest value with at least `share` of the values <= it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


@dataclass(frozen=True)
class Row:
    """One model's numbers on the common questions."""

    model: Model
    questions: int
    passed: int  # QueryMind match
    ex: int  # BIRD EX
    p50: float
    p95: float
    input_tokens: float  # mean per question
    output_tokens: float
    cost: float | None  # dollars per question; None = no price for this model

    @property
    def cost_per_correct(self) -> float | None:
        if self.cost is None or not self.passed:
            return None
        return self.cost * self.questions / self.passed


def row(run: ModelRun, ids: set[str], prices: Prices) -> Row:
    chosen = [o for o in run.outcomes if o.item.id in ids]
    seconds = [o.seconds for o in chosen]
    tokens_in = statistics.mean(o.input_tokens for o in chosen)
    tokens_out = statistics.mean(o.output_tokens for o in chosen)
    price = prices.models.get(run.model.llm)
    cost = None if price is None else (tokens_in * price.input + tokens_out * price.output) / 1e6
    return Row(
        model=run.model,
        questions=len(chosen),
        passed=sum(o.passed for o in chosen),
        ex=sum(o.ex for o in chosen),
        p50=statistics.median(seconds),
        p95=percentile(seconds, 0.95),
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cost=cost,
    )


def table(study: str, runs: Sequence[ModelRun], prices: Prices,
          report_dir: Path = REPORT_DIR, suites: Sequence[str] = ()) -> str:  # fmt: skip
    """The Markdown section for one study: one row per model that has every suite."""
    ids = common_ids(runs)
    complete = [r for r in runs if not r.missing]
    title = {"golden": "Golden 58 (Northwind 30 + business 13 + Chinook 15)",
             "bird": "BIRD-100 (the fixed subset, evidence on)"}[study]  # fmt: skip
    if suites and set(suites) != set(STUDY_SUITES[study]):
        title += f": so far only {', '.join(suites)}"  # until every model has run them all
    lines = [f"## {title}", ""]
    if not complete or not ids:
        lines.append("No model has a complete run yet.")
    else:
        bird = study == "bird"
        lines += [
            f"Every row is scored on the same **{len(ids)} questions**: those every model below "
            "scored (a question lost to a rate limit is left out for all).",
            "",
            "| Model | " + ("BIRD EX | " if bird else "") + "QueryMind match | p50 s | p95 s "
            "| Tokens / q (in + out) | $ / question | $ / 1,000 q | $ / correct answer |",
            "|---|" + ("---|" if bird else "") + "---|---|---|---|---|---|---|",
        ]
        for r in sorted((row(run, ids, prices) for run in complete),
                        key=lambda r: (-(r.ex if bird else r.passed), r.cost or 0)):  # fmt: skip
            ex = f"{r.ex}/{r.questions} | " if bird else ""
            lines.append(
                f"| {r.model.llm} | {ex}{r.passed}/{r.questions} | {r.p50:.1f} | {r.p95:.1f} "
                f"| {r.input_tokens:,.0f} + {r.output_tokens:,.0f} | {_dollars(r.cost)} "
                f"| {_dollars(None if r.cost is None else r.cost * 1000)} "
                f"| {_dollars(r.cost_per_correct)} |"
            )
        if len(complete) == 1:
            lines += ["", "Only one model has run this study so far: nothing to compare yet."]
    lines += ["", "**Reports:**"]
    for run in runs:
        links = " · ".join(
            f"[{p.stem}]({p.relative_to(report_dir).as_posix()})" for p in run.reports
        )
        not_run = f" (not run yet: {', '.join(run.missing)})" if run.missing else ""
        lines.append(f"- {run.model.llm}: {links or '-'}{not_run}")
    return "\n".join(lines)


def _dollars(value: float | None) -> str:
    if value is None:
        return "-"
    return f"${value:.5f}" if value < 0.01 else f"${value:.2f}"


def build_report(sections: Sequence[str], prices: Prices) -> str:
    notes = [
        "# Phase 4.6: model comparison (our harness)",
        "",
        "Same agent (4.4 arm D: 3 SQL tries, semantic layer, schema retrieval, few-shot), one "
        "question at a time; only the LLM changes.",
        "",
        f"- **Cost** = list-price equivalent, paid tier, prices checked {prices.checked} "
        "(`evals/prices.yaml`, with sources); our runs were on free tiers. It covers the SQL "
        "path the eval runs: the user-facing summary adds one more call per answer, and the "
        "embeddings are the same for every model.",
        "- **Latency** = end to end on free tiers, one question at a time. Groq and Google run "
        "different hardware, so it compares services, not only models. p50 is the headline.",
        "- **Noise:** one run per model. Two runs of the same setup differ by about 1-2 "
        "questions on 58 and about 3 on BIRD-100, so smaller gaps aren't real.",
    ]
    return "\n".join([*notes, "", *("\n\n".join(sections).splitlines())]) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare LLMs on the same agent (4.6).")
    commands = parser.add_subparsers(dest="action", required=True)
    run_parser = commands.add_parser("run", help="run one model (real LLM calls)")
    run_parser.add_argument("model", choices=MODELS)
    run_parser.add_argument(
        "--suites", nargs="+", choices=[s for suites in STUDY_SUITES.values() for s in suites],
        default=list(STUDY_SUITES["golden"]), help="default: the golden 58",
    )  # fmt: skip
    run_parser.add_argument("--force", action="store_true", help="run again even if saved")
    table_parser = commands.add_parser("table", help="build the table from the saved reports")
    table_parser.add_argument("--out", type=Path, default=REPORT_DIR / "phase4-models.md")
    table_parser.add_argument(
        "--suites", nargs="+", choices=STUDY_SUITES["golden"], default=[],
        help="golden study: only these suites, until every model has run all three",
    )  # fmt: skip
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # Windows console

    if args.action == "run":
        return run(MODELS[args.model], args.suites, force=args.force)
    try:
        prices = load_prices()
        sections = [
            table(study, collect(study, suites=suites), prices, suites=suites)
            for study, suites in (("golden", args.suites), ("bird", []))
        ]
    except (ModelsError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    text = build_report(sections, prices)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
