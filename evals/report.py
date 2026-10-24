"""Eval results: one Outcome per question, the summary scores, and the reports (Markdown + JSON).

    summary = summarize_outcomes(outcomes)
    write_reports(Path("docs/eval-reports/x.md"), outcomes, title=..., meta=...)  # x.md + x.json
    report = load_report(Path("docs/eval-reports/x.json"))                      # back to Outcomes

Two scores for every question (4.2):
  - **QueryMind match** (`passed`, evals/compare.py `compare_results`): the same answer as a
    person would judge it (names, column order and number noise don't matter).
  - **BIRD EX** (`ex`, `bird_ex`): BIRD's official rule, a set of rows compared exactly. This is
    the number to compare with the BIRD leaderboard.
They disagree on purpose in a few cases; the report counts where (see "Where the two disagree").

The JSON keeps every question with its SQL, so a scoring change can be checked on old runs
without new LLM calls (evals/rescore.py).
"""

import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from evals.golden_set import GoldenQuestion

TIERS = ("easy", "medium", "hard")
CONFIDENCE_LEVELS = ("high", "medium", "low")
REPORT_FORMAT = 1  # bump when the JSON shape changes


@dataclass(frozen=True)
class Outcome:
    """How QueryMind did on one question."""

    item: GoldenQuestion
    passed: bool  # same answer as the reference (extra columns allowed)
    strict: bool  # ...and no extra columns
    reason: str  # why it failed ("" if it passed)
    error: str | None  # the exception type, if the pipeline failed instead of answering
    sql: str | None  # the SQL QueryMind ran (None if it never got that far)
    seconds: float
    provider_attempts: int  # calls to ask() for this question (> 1 after a rate-limit retry)
    sql_attempts: int  # SQL queries the agent tried (> 1 = it corrected itself); 0 = none
    confidence: str | None  # the answer's confidence level; None when there was no answer
    # False = no verdict: the LLM provider stayed unavailable (e.g. a daily rate limit), so the
    # agent never got to answer. Left out of the accuracy, and listed for a rerun instead.
    scored: bool = True
    # What the LLM was shown (3.3): "full", "retrieved" or "fallback"; tables; size in tokens.
    schema_mode: str | None = None
    schema_tables: int = 0
    schema_tokens: int = 0
    # BIRD's official execution accuracy (4.2): same set of rows, exactly. False with no answer.
    ex: bool = False
    ex_reason: str = ""
    database: str | None = None  # the Postgres database it ran in (None = the one in .env)
    # What the question cost (4.2): LLM calls and tokens, as the provider reported them.
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class Score:
    passed: int
    total: int

    @property
    def percent(self) -> float:
        return 100.0 * self.passed / self.total if self.total else 0.0

    def __str__(self) -> str:
        return f"{self.passed}/{self.total} ({self.percent:.1f}%)"


@dataclass(frozen=True)
class Summary:
    accuracy: Score  # QueryMind match, extra columns allowed: the headline for our own sets
    strict: Score
    ex: Score  # BIRD's official EX: the headline for BIRD
    by_tier: dict[str, tuple[Score, Score]]  # tier → (match, EX)
    by_database: dict[str, tuple[Score, Score]]  # database → (match, EX); one entry = one database
    by_tag: dict[str, Score]
    # Scored questions by verdict pair: both pass, only our match, only EX, neither.
    agreement: dict[str, int]
    errors: dict[str, int]  # exception type → how many scored questions failed with it
    not_scored: list[str]  # ids of questions with no verdict (LLM or database unavailable)
    corrected: list[str]  # ids that passed only after the agent fixed its own SQL
    # Calibration: accuracy among the answers at each confidence level. A useful score has
    # high >= medium >= low; answers that never came (errors, clarifications) aren't counted.
    by_confidence: dict[str, Score]
    seconds: float
    # The schema the LLM saw (3.3): mean size over the questions that got one, and the ids
    # whose retrieval failed and fell back to the whole schema (they don't count as retrieval).
    schema_tokens: float = 0.0
    schema_modes: dict[str, int] | None = None
    fallbacks: tuple[str, ...] = ()
    # Cost (4.2), per scored question: mean LLM calls, input and output tokens; and the totals.
    llm_calls: float = 0.0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    total_tokens: int = 0


AGREEMENT = {
    (True, True): "both pass",
    (True, False): "only QueryMind match",
    (False, True): "only BIRD EX",
    (False, False): "both fail",
}


def summarize_outcomes(outcomes: Sequence[Outcome]) -> Summary:
    def score(group: Iterable[Outcome], *, measure: str = "passed") -> Score:
        group = [o for o in group if o.scored]  # unscored questions count neither way
        return Score(sum(bool(getattr(o, measure)) for o in group), len(group))

    def both(group: list[Outcome]) -> tuple[Score, Score]:
        return score(group), score(group, measure="ex")

    scored = [o for o in outcomes if o.scored]
    tags = sorted({tag for o in outcomes for tag in o.item.tags})
    databases = sorted({o.database for o in outcomes if o.database})
    shown = [o for o in outcomes if o.schema_mode]
    agreement = Counter(AGREEMENT[(o.passed, o.ex)] for o in scored)
    return Summary(
        accuracy=score(outcomes),
        strict=score(outcomes, measure="strict"),
        ex=score(outcomes, measure="ex"),
        by_tier={tier: both([o for o in outcomes if o.item.tier == tier]) for tier in TIERS},
        by_database={db: both([o for o in outcomes if o.database == db]) for db in databases},
        by_tag={tag: score(o for o in outcomes if tag in o.item.tags) for tag in tags},
        agreement={label: agreement[label] for label in AGREEMENT.values()},
        errors=dict(Counter(o.error for o in scored if o.error)),
        not_scored=[o.item.id for o in outcomes if not o.scored],
        corrected=[o.item.id for o in outcomes if o.passed and o.sql_attempts > 1],
        by_confidence={
            level: score(o for o in outcomes if o.confidence == level)
            for level in CONFIDENCE_LEVELS
        },
        seconds=sum(o.seconds for o in outcomes),
        schema_tokens=sum(o.schema_tokens for o in shown) / len(shown) if shown else 0.0,
        schema_modes=dict(Counter(o.schema_mode for o in shown if o.schema_mode)),
        fallbacks=tuple(o.item.id for o in shown if o.schema_mode == "fallback"),
        llm_calls=_mean(o.llm_calls for o in scored),
        input_tokens=_mean(o.input_tokens for o in scored),
        output_tokens=_mean(o.output_tokens for o in scored),
        total_tokens=sum(o.input_tokens + o.output_tokens for o in outcomes),
    )


def _mean(values: Iterable[int]) -> float:
    numbers = list(values)
    return sum(numbers) / len(numbers) if numbers else 0.0


def status(outcome: Outcome) -> str:
    if not outcome.scored:
        return "SKIP"
    if outcome.passed:
        return "PASS"
    return "ERROR" if outcome.error else "FAIL"


def build_report(
    outcomes: Sequence[Outcome],
    *,
    title: str,
    meta: dict[str, str],
    run_flags: str = "--suite golden",
    reference_failures: dict[str, str] | None = None,
    tier_names: dict[str, str] | None = None,
) -> str:
    """A markdown report: scores, breakdowns, then every failure with both SQLs.

    `reference_failures`: questions whose reference SQL itself failed (id → why). They were
    not asked, and are listed so they are never silently dropped. `tier_names` renames the
    tiers in the tables (BIRD: easy → simple, medium → moderate, hard → challenging).
    """
    summary = summarize_outcomes(outcomes)
    names = tier_names or {}
    lines = [f"# {title}", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += [
        "",
        "## Score",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| **QueryMind match** (extra columns allowed) | **{summary.accuracy}** |",
        f"| **BIRD EX** (official: same set of rows, exactly) | **{summary.ex}** |",
        f"| Strict match (exactly the asked-for columns) | {summary.strict} |",
        f"| Passed after self-correction (SQL retried) | {len(summary.corrected)} |",
        f"| Not scored (LLM or database unavailable) | {len(summary.not_scored)} |",
        f"| Schema shown (mean size) | {modes_text(summary)}, "
        f"~{summary.schema_tokens:,.0f} tokens |",
        f"| Retrieval fell back to the whole schema | {len(summary.fallbacks)} |",
        f"| LLM calls per question (mean) | {summary.llm_calls:.1f} |",
        f"| Tokens per question (mean in / out) | {summary.input_tokens:,.0f} / "
        f"{summary.output_tokens:,.0f} |",
        f"| Tokens in total (LLM calls only: no embeddings; a failed call counts 0) | "
        f"{summary.total_tokens:,} |",
        f"| Total time | {summary.seconds:.0f} s |",
        "",
        "## By tier",
        "",
        "| Tier | QueryMind match | BIRD EX |",
        "|---|---|---|",
        *(
            f"| {names.get(tier, tier)} | {match} | {ex} |"
            for tier, (match, ex) in summary.by_tier.items()
        ),
        "",
    ]
    if len(summary.by_database) > 1:
        lines += [
            "## By database",
            "",
            "| Database | QueryMind match | BIRD EX |",
            "|---|---|---|",
            *(f"| {db} | {match} | {ex} |" for db, (match, ex) in summary.by_database.items()),
            "",
        ]
    lines += [
        "## Where the two disagree",
        "",
        "Scored questions by verdict. *Only QueryMind match*: e.g. other column order, extra",
        "columns, rounding. *Only BIRD EX*: e.g. duplicate rows (a set has none).",
        "",
        "| Verdict | Questions |",
        "|---|---|",
        *(f"| {label} | {count} |" for label, count in summary.agreement.items()),
        "",
        "## By confidence (calibration)",
        "",
        "Accuracy among the answers given at each level. High should be the most accurate.",
        "",
        "| Confidence | Passed |",
        "|---|---|",
        *(f"| {level} | {score} |" for level, score in summary.by_confidence.items()),
        "",
    ]
    if summary.by_tag:
        lines += [
            "## By skill (tag)",
            "",
            "| Tag | Passed |",
            "|---|---|",
            *(f"| {tag} | {score} |" for tag, score in summary.by_tag.items()),
            "",
        ]
    if summary.not_scored:
        lines += [
            "## Not scored",
            "",
            "The LLM provider or the database was unavailable after every retry (usually a rate",
            "limit), so the agent never answered. They are left out of the accuracy above. Rerun:",
            "",
            "```",
            f"uv run python -m evals.run {run_flags} --ids {' '.join(summary.not_scored)}",
            "```",
            "",
        ]
    if reference_failures:
        lines += [
            "## Reference SQL failed (not asked)",
            "",
            "The reference SQL could not run through our validator and executor, so there is",
            "nothing to compare with. These questions are left out of the accuracy above.",
            "",
            "| Question | Why |",
            "|---|---|",
            *(f"| `{qid}` | {why} |" for qid, why in sorted(reference_failures.items())),
            "",
        ]
    if summary.errors:
        lines += ["## Errors (no answer at all)", "", "| Error | Questions |", "|---|---|"]
        lines += [f"| {name} | {count} |" for name, count in sorted(summary.errors.items())]
        lines.append("")

    lines += [
        "## All questions",
        "",
        "| Question | Tier | Result | BIRD EX | SQL tries | Confidence | Schema | Tokens | Time "
        "| Why |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        result = status(o)
        if o.passed and not o.strict:
            result += " (extra columns)"
        ex = "-" if not o.scored else ("PASS" if o.ex else "FAIL")
        why = _cell(o.reason or (o.ex_reason if o.scored and not o.ex else ""))
        schema = (
            f"{o.schema_mode}: {o.schema_tables} tables, ~{o.schema_tokens:,}"
            if o.schema_mode
            else "-"
        )
        lines.append(
            f"| `{o.item.id}` | {names.get(o.item.tier, o.item.tier)} | {result} | {ex} "
            f"| {o.sql_attempts or '-'} | {o.confidence or '-'} | {schema} "
            f"| {o.input_tokens + o.output_tokens:,} | {o.seconds:.1f} s | {why} |"
        )

    failures = [o for o in outcomes if o.scored and not (o.passed and o.ex)]
    if failures:
        lines += ["", "## Failures in detail", "", "Every question that failed either score."]
    for o in failures:
        verdicts = f"match {'PASS' if o.passed else 'FAIL'}, BIRD EX {'PASS' if o.ex else 'FAIL'}"
        lines += [
            "",
            f"### `{o.item.id}` ({names.get(o.item.tier, o.item.tier)}): "
            f"{o.error or 'wrong result'} ({verdicts})",
            "",
            f"**Question:** {o.item.question}",
            "",
            f"**Why:** {o.reason or '-'} · **BIRD EX:** {o.ex_reason or 'pass'}",
            "",
            "**QueryMind's SQL:**"
            if o.sql
            else "**QueryMind's SQL:** none (failed before running)",
        ]
        if o.sql:
            lines += ["", "```sql", o.sql, "```"]
        lines += ["", "**Reference SQL:**", "", "```sql", o.item.sql.strip(), "```"]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    """Text safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def modes_text(summary: Summary) -> str:
    """E.g. "retrieved 14, fallback 1": how many questions saw which kind of schema."""
    modes = summary.schema_modes or {}
    return ", ".join(f"{mode} {count}" for mode, count in sorted(modes.items())) or "-"


# --- JSON: the machine-readable twin of the markdown report --------------------------------


@dataclass(frozen=True)
class SavedReport:
    """A JSON report read back: what was run, and every question's outcome."""

    meta: dict[str, str]
    outcomes: list[Outcome]
    reference_failures: dict[str, str]
    tier_names: dict[str, str]
    max_rows: int  # the row cap the run used (rescore must use the same)


def outcome_to_json(outcome: Outcome) -> dict[str, Any]:
    record: dict[str, Any] = {
        f.name: getattr(outcome, f.name) for f in fields(outcome) if f.name != "item"
    }
    record["item"] = outcome.item.model_dump(mode="json")
    return record


def outcome_from_json(record: dict[str, Any]) -> Outcome:
    values = dict(record)
    item = GoldenQuestion.model_validate(values.pop("item"))
    return Outcome(item=item, **values)


def report_json(
    outcomes: Sequence[Outcome],
    *,
    meta: dict[str, str],
    max_rows: int,
    reference_failures: dict[str, str] | None = None,
    tier_names: dict[str, str] | None = None,
) -> dict[str, Any]:
    summary = summarize_outcomes(outcomes)
    return {
        "format": REPORT_FORMAT,
        "meta": meta,
        "max_rows": max_rows,
        "tier_names": tier_names or {},
        "summary": {
            "match": [summary.accuracy.passed, summary.accuracy.total],
            "bird_ex": [summary.ex.passed, summary.ex.total],
            "strict": [summary.strict.passed, summary.strict.total],
            "not_scored": summary.not_scored,
            "agreement": summary.agreement,
            "mean_input_tokens": round(summary.input_tokens, 1),
            "mean_output_tokens": round(summary.output_tokens, 1),
            "total_tokens": summary.total_tokens,
        },
        "reference_failures": reference_failures or {},
        "outcomes": [outcome_to_json(o) for o in outcomes],
    }


def write_reports(
    path: Path,
    outcomes: Sequence[Outcome],
    *,
    title: str,
    meta: dict[str, str],
    max_rows: int,
    run_flags: str = "--suite golden",
    reference_failures: dict[str, str] | None = None,
    tier_names: dict[str, str] | None = None,
) -> Path:
    """Write `path` (markdown) and the same name with .json next to it. Returns the JSON path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    markdown = build_report(
        outcomes,
        title=title,
        meta=meta,
        run_flags=run_flags,
        reference_failures=reference_failures,
        tier_names=tier_names,
    )
    path.write_text(markdown, encoding="utf-8")
    data = report_json(
        outcomes,
        meta=meta,
        max_rows=max_rows,
        reference_failures=reference_failures,
        tier_names=tier_names,
    )
    json_path = path.with_suffix(".json")
    json_path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return json_path


def load_report(path: Path) -> SavedReport:
    """Read a JSON report back.

    Raises:
        ValueError: not a report of this format.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("format") != REPORT_FORMAT:
        raise ValueError(f"{path}: not a QueryMind eval report (format {REPORT_FORMAT})")
    return SavedReport(
        meta=data["meta"],
        outcomes=[outcome_from_json(record) for record in data["outcomes"]],
        reference_failures=data["reference_failures"],
        tier_names=data["tier_names"],
        max_rows=data["max_rows"],
    )
