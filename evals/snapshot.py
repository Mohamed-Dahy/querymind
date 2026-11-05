"""Turn the saved eval reports into one committed file, for the evals lab (UI U5.1).

    uv run python -m evals.snapshot     # docs/eval-reports/ → evals/snapshot.json

The reports are git-ignored, so a fresh clone or a deployed app has none. This file carries what the
page needs, under three rules:

1. **Named fields only.** Each value is copied by name into a model with no field for anything else.
   Reports also hold result rows (`context`), answers (`summary`) and judge reasons that quote rows.
   Skipping those by name would let the next new field through; copying by name can't.
2. **One calculation.** The ablation and model numbers come from the same functions that write the
   Markdown tables (`arm_rows`, `row`), so the page and the reports can't disagree.
3. **Same reports, same bytes.** No timestamp, sorted keys, LF: a diff means the numbers changed.

BIRD's questions are CC BY-SA data, so only their ids are written (the API adds the text where the
downloaded file exists: `querymind/api/evals.py`).
"""

import argparse
import json
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from evals import ablation
from evals import models as model_study
from evals.gate import ALLOWED_REGRESSIONS, BASELINE_FILE, Baseline
from evals.quality import QualityRun, load_run, metric_summaries, retrieval_summary
from evals.report import Outcome, SavedReport, Score, load_report, summarize_outcomes
from querymind.api.evals import (
    AblationArm,
    AblationStudy,
    AblationView,
    BirdInfo,
    EvalBreakdown,
    EvalOutcome,
    EvalQuestion,
    EvalRun,
    EvalScore,
    EvalsReply,
    GateInfo,
    Milestone,
    ModelComparison,
    ModelNotRun,
    ModelRow,
    ModelStudy,
    QualityMetric,
    QualityPanel,
    QualityQuestion,
    QualityScore,
    RetrievalQuality,
    SpeedUp,
)

SNAPSHOT_FILE = Path(__file__).parent / "snapshot.json"
#: The web UI's fake API serves the same bytes, so its tests see the real numbers.
MOCK_FILE = Path(__file__).parents[1] / "web" / "src" / "mocks" / "recorded" / "evals.json"
MILESTONES_FILE = Path(__file__).parent / "milestones.yaml"
REPORT_DIR = Path("docs/eval-reports")
QUALITY_REPORT = "phase4-quality"
#: The same setup as arm C with 4 workers: in the matrix, but its times include limiter waits.
PARALLEL_RUN = "phase4-bird-100-w4"
MAX_REASON = 200

StudyName = Literal["golden", "bird"]


class SnapshotError(Exception):
    """The reports can't make a snapshot (none found, or a report that doesn't fit)."""


class _GateFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    baseline_date: str
    failure_proven: bool
    note: str
    actions_url: str


class _HandWritten(BaseModel):
    """evals/milestones.yaml."""

    model_config = ConfigDict(extra="forbid")

    milestones: list[Milestone]
    speed_ups: list[SpeedUp]
    noise_questions: dict[str, int]
    quality_notes: dict[str, str]
    gate: _GateFacts
    bird: BirdInfo


def load_hand_written(path: Path = MILESTONES_FILE) -> _HandWritten:
    return _HandWritten.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def clean_reason(outcome: Outcome) -> str:
    """Why it failed, in words safe for a public file.

    Provider errors hold the vendor's raw reply, and an output error holds the LLM's raw text, so
    both become fixed words. Other reasons are short by nature; the cap is there for the day one
    isn't.
    """
    if outcome.error == "LLMProviderError":
        return "The LLM provider was unavailable"
    if outcome.error == "LLMOutputError":
        return "The LLM's output didn't match the schema"
    if not outcome.scored:
        return "Not scored: the database was unavailable"
    return outcome.reason[:MAX_REASON]


def _score(score: Score) -> EvalScore:
    return EvalScore(passed=score.passed, total=score.total)


def _outcome(outcome: Outcome) -> EvalOutcome:
    return EvalOutcome(
        question_id=outcome.item.id,
        scored=outcome.scored,
        passed=outcome.passed,
        ex=outcome.ex,
        reason=clean_reason(outcome),
        ex_reason=outcome.ex_reason[:MAX_REASON],
        sql=outcome.sql,
        sql_attempts=outcome.sql_attempts,
        seconds=round(outcome.seconds, 1),
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
    )


@dataclass(frozen=True)
class _Source:
    """One report that goes in the snapshot."""

    stem: str
    label: str
    study: StudyName
    suite: str
    latency_valid: bool = True


def _study_of(suite: str) -> StudyName:
    return "bird" if suite == "bird" else "golden"


def run_sources() -> Iterator[_Source]:
    """The chosen reports: every ablation arm, the models and the parallel run. Nothing else.

    Trial runs (bird-20, golden-2) and the CI baseline's 3 questions aren't comparable, so they're
    left out by not being named here.
    """
    for study in ablation.STUDIES.values():
        for arm in study.arms:
            for suite in study.suites:
                stem = ablation.report_path(study, arm, suite).stem
                yield _Source(stem, f"Arm {arm.key}", _study_of(suite), suite)
    for model in model_study.MODELS.values():
        for suites in model_study.STUDY_SUITES.values():
            for suite in suites:
                if suite not in model.reuse:  # a reused report is already an ablation arm
                    stem = model_study.report_path(model, suite).stem
                    yield _Source(stem, model.key, _study_of(suite), suite)
    yield _Source(PARALLEL_RUN, "Arm C, 4 workers", "bird", "bird", latency_valid=False)


def _breakdown(groups: dict[str, tuple[Score, Score]]) -> list[EvalBreakdown]:
    return [
        EvalBreakdown(name=name, match=_score(match), ex=_score(ex))
        for name, (match, ex) in groups.items()
        if match.total
    ]


def _run(source: _Source, saved: SavedReport) -> EvalRun:
    summary = summarize_outcomes(saved.outcomes)
    names = saved.tier_names
    tiers = {names.get(tier, tier): scores for tier, scores in summary.by_tier.items()}
    return EvalRun(
        key=source.stem,
        label=source.label,
        study=source.study,
        suite=source.suite,
        llm=saved.meta.get("LLM", "unknown"),
        date=saved.meta.get("Date", ""),
        code=saved.meta.get("Code", ""),
        wall_time=saved.meta.get("Wall time"),
        latency_valid=source.latency_valid,
        match=_score(summary.accuracy),
        ex=_score(summary.ex),
        not_scored=summary.not_scored,
        by_tier=_breakdown(tiers),
        by_database=_breakdown(summary.by_database),
        outcomes=[_outcome(o) for o in saved.outcomes],
    )


def _questions(
    sources: Sequence[tuple[_Source, SavedReport]],
) -> list[EvalQuestion]:
    """One entry per question. BIRD's text and reference SQL are never written (see the top)."""
    found: dict[str, EvalQuestion] = {}
    ordered = sorted(sources, key=lambda pair: (pair[1].meta.get("Date", ""), pair[0].stem))
    for source, saved in ordered:  # oldest first, so a reworded question ends up as it is now
        names = saved.tier_names
        for o in saved.outcomes:
            bird = source.study == "bird"
            found[o.item.id] = EvalQuestion(
                id=o.item.id,
                study=source.study,
                suite=source.suite,
                tier=names.get(o.item.tier, o.item.tier),
                database=o.database,
                question=None if bird else o.item.question,
                reference_sql=None if bird else o.item.sql.strip(),
            )
    return [found[qid] for qid in sorted(found)]


def _arm(row: ablation.ArmRow | None, result: ablation.ArmResult) -> AblationArm:
    base = AblationArm(
        key=result.arm.key,
        adds=result.arm.adds,
        match=None,
        ex=None,
        delta_points=None,
        not_scored=0,
        repairs_per_question=None,
        input_tokens=None,
        output_tokens=None,
        median_seconds=None,
        reports=[p.stem for p in result.reports],
        missing=result.missing,
    )
    if row is None:
        return base
    return base.model_copy(
        update={
            "match": _score(row.match),
            "ex": _score(row.ex),
            "delta_points": None if row.delta is None else round(row.delta, 1),
            "not_scored": row.not_scored,
            "repairs_per_question": round(row.repairs, 2),
            "input_tokens": round(row.input_tokens),
            "output_tokens": round(row.output_tokens),
            "median_seconds": round(row.median_seconds, 1),
        }
    )


def _ablation_view(scope: str, study: ablation.Study, report_dir: Path) -> AblationView:
    results = ablation.collect(study, report_dir)
    rows = ablation.arm_rows(study, results)
    return AblationView(
        scope=scope, arms=[_arm(row, result) for row, result in zip(rows, results, strict=True)]
    )


def _ablations(report_dir: Path, noise: dict[str, int]) -> list[AblationStudy]:
    studies = []
    for study in ablation.STUDIES.values():
        results = ablation.collect(study, report_dir)
        views = [_ablation_view("all", study, report_dir)]
        if len(study.suites) > 1:  # with one suite, "all" already is that suite
            views += [
                _ablation_view(suite, replace(study, suites=(suite,)), report_dir)
                for suite in study.suites
            ]
        studies.append(
            AblationStudy(
                name=study.name,
                title=study.title,
                headline="ex" if study.headline == "ex" else "match",
                llm=next((r.llm for r in results if r.llm), None),
                noise_questions=noise[study.name],
                views=views,
            )
        )
    return studies


def suites_to_compare(study: str, report_dir: Path) -> tuple[str, ...]:
    """The largest set of a study's suites that at least two models finished, or all of them.

    The Markdown table takes these from a flag typed by hand; this reads them from the data, so
    "so far only golden, business" disappears by itself once every model has run every suite.
    """
    suites = model_study.STUDY_SUITES[study]
    has = {
        model.key: {
            suite
            for suite in suites
            if model_study.report_path(model, suite, report_dir).with_suffix(".json").exists()
        }
        for model in model_study.MODELS.values()
    }
    # Each model's finished suites are the candidates; the widest that two models share wins.
    candidates = sorted(
        {frozenset(a & b) for x, a in has.items() for y, b in has.items() if x < y},
        key=len,
        reverse=True,
    )
    best = next((c for c in candidates if c), frozenset(suites))
    return tuple(suite for suite in suites if suite in best)


def _model_study(study: str, report_dir: Path, prices: model_study.Prices) -> ModelStudy:
    all_suites = model_study.STUDY_SUITES[study]
    suites = suites_to_compare(study, report_dir)
    runs = model_study.collect(study, report_dir, suites=suites)
    ids = model_study.common_ids(runs)
    complete = [r for r in runs if not r.missing]
    computed = [model_study.row(r, ids, prices) for r in complete] if ids else []
    bird = study == "bird"
    computed.sort(key=lambda r: (-(r.ex if bird else r.passed), r.cost or 0))
    partial = set(suites) != set(all_suites)
    return ModelStudy(
        name=study,
        title=model_study.STUDY_TITLES[study],
        suites=list(suites),
        partial_note=f"so far only {', '.join(suites)}" if partial else None,
        questions=len(ids),
        only_one_model=len(complete) == 1,
        rows=[_model_row(r, prices) for r in computed],
        not_run=[ModelNotRun(llm=r.model.llm, missing=r.missing) for r in runs if r.missing],
        reports=sorted(p.stem for r in runs for p in r.reports),
    )


def _model_row(row: model_study.Row, prices: model_study.Prices) -> ModelRow:
    price = prices.models.get(row.model.llm)
    return ModelRow(
        llm=row.model.llm,
        questions=row.questions,
        passed=row.passed,
        ex=row.ex,
        p50_seconds=round(row.p50, 1),
        p95_seconds=round(row.p95, 1),
        input_tokens=round(row.input_tokens),
        output_tokens=round(row.output_tokens),
        cost_per_question=None if row.cost is None else round(row.cost, 8),
        cost_per_correct=None if row.cost_per_correct is None else round(row.cost_per_correct, 8),
        price_note=price.note if price else "",
    )


def _models(report_dir: Path) -> ModelComparison:
    prices = model_study.load_prices()
    return ModelComparison(
        prices_checked=prices.checked.isoformat(),
        notes=[
            note.removeprefix("- ").replace("**", "").replace("`", "")
            for note in model_study.report_notes(prices)
        ],
        studies=[_model_study(study, report_dir, prices) for study in model_study.STUDY_SUITES],
    )


def _quality(run: QualityRun, notes: dict[str, str]) -> QualityPanel | None:
    """Scores only. `answers[].context`, `.summary` and the judge's reasons quote data rows."""
    if not run.answers:
        return None
    sql_match = {case.id: case.sql_match for case in run.answers}
    questions = [
        QualityQuestion(
            question_id=judged.id,
            sql_match=sql_match.get(judged.id),
            scores=[
                QualityScore(metric=v.metric, score=v.score, passed=v.passed)
                for v in judged.verdicts
            ],
            note=notes.get(judged.id, ""),
        )
        for judged in sorted(run.judged, key=lambda j: j.id)
    ]
    retrieval = retrieval_summary(run.retrieval)
    return QualityPanel(
        report=QUALITY_REPORT,
        date=run.meta.get("Date", ""),
        code=run.meta.get("Code", ""),
        agent_llm=run.meta.get("Agent LLM", "unknown"),
        judge_llm=run.meta.get("Judge LLM", "unknown"),
        answers=len(run.answers),
        judged_answers=sum(bool(case.summary) for case in run.answers),
        metrics=[
            QualityMetric(
                metric=m.metric,
                asks=m.asks,
                mean=None if m.mean is None else round(m.mean, 3),
                passed=m.passed,
                judged=m.judged,
                not_scored=m.not_scored,
            )
            for m in metric_summaries(run)
        ],
        questions=questions,
        retrieval=None
        if retrieval is None
        else RetrievalQuality(
            questions=retrieval.questions,
            complete=retrieval.complete,
            id_recall=round(retrieval.id_recall, 3),
            id_precision=round(retrieval.id_precision, 3),
            sample=retrieval.sample,
            llm_precision=(
                None if retrieval.llm_precision is None else round(retrieval.llm_precision, 3)
            ),
            id_precision_sample=(
                None
                if retrieval.id_precision_sample is None
                else round(retrieval.id_precision_sample, 3)
            ),
        ),
    )


def _gate(facts: _GateFacts, baseline_path: Path) -> GateInfo:
    baseline = Baseline.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    return GateInfo(
        llm=baseline.llm,
        questions=len(baseline.passed) + len(baseline.failed),
        passed=len(baseline.passed),
        failed=len(baseline.failed),
        allowed_regressions=ALLOWED_REGRESSIONS,
        baseline_date=facts.baseline_date,
        failure_proven=facts.failure_proven,
        note=facts.note,
        actions_url=facts.actions_url,
    )


def _phase4_milestones(
    studies: Sequence[AblationStudy], runs: Sequence[EvalRun]
) -> list[Milestone]:
    """Phase 4's own points, from the tables: its numbers are never typed by hand."""
    points = []
    for study in studies:
        arm = next((a for a in reversed(study.views[0].arms) if a.match is not None), None)
        if arm is None:
            continue
        bird = study.headline == "ex"
        score = arm.ex if bird else arm.match
        assert score is not None  # the arm has run, so it has both scores
        dates = [run.date[:10] for run in runs if run.key in arm.reports]
        points.append(
            Milestone(
                phase="4",
                title=f"Arm {arm.key}, the full agent" + (" (BIRD EX)" if bird else ""),
                question_set=study.title.split(":")[0],
                passed=score.passed,
                total=score.total,
                not_scored=arm.not_scored,
                llm=study.llm or "unknown",
                date=max(dates, default=""),
                report=", ".join(arm.reports),
            )
        )
    return points


def build_snapshot(
    report_dir: Path = REPORT_DIR,
    *,
    milestones_path: Path = MILESTONES_FILE,
    baseline_path: Path = BASELINE_FILE,
) -> EvalsReply:
    """Everything the evals lab shows. Raises SnapshotError if there are no reports to read."""
    sources: list[tuple[_Source, SavedReport]] = []
    for source in run_sources():
        path = report_dir / f"{source.stem}.json"
        if path.exists() and all(source.stem != kept.stem for kept, _ in sources):
            sources.append((source, load_report(path)))
    if not sources:
        raise SnapshotError(f"no eval reports in {report_dir}")
    hand = load_hand_written(milestones_path)
    runs = [_run(source, saved) for source, saved in sources]
    studies = _ablations(report_dir, hand.noise_questions)
    return EvalsReply(
        bird=hand.bird,
        questions=_questions(sources),
        runs=sorted(runs, key=lambda run: run.key),
        ablations=studies,
        models=_models(report_dir),
        quality=_quality(load_run(report_dir / f"{QUALITY_REPORT}.json"), hand.quality_notes),
        gate=_gate(hand.gate, baseline_path),
        milestones=[*hand.milestones, *_phase4_milestones(studies, runs)],
        speed_ups=hand.speed_ups,
    )


def render(snapshot: EvalsReply) -> str:
    """The snapshot as text that only changes when the numbers do."""
    data = snapshot.model_dump(mode="json")
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def write_snapshot(snapshot: EvalsReply, path: Path = SNAPSHOT_FILE) -> None:
    # Bytes, so Windows doesn't turn \n into \r\n and make the same numbers look changed.
    path.write_bytes(render(snapshot).encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write evals/snapshot.json from the reports.")
    parser.add_argument("--reports", type=Path, default=REPORT_DIR)
    parser.add_argument("--out", type=Path, default=SNAPSHOT_FILE)
    args = parser.parse_args(argv)
    try:
        snapshot = build_snapshot(args.reports)
    except (
        SnapshotError,
        ablation.AblationError,
        model_study.ModelsError,
        OSError,
        ValueError,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    write_snapshot(snapshot, args.out)
    if args.out == SNAPSHOT_FILE:
        write_snapshot(snapshot, MOCK_FILE)
    print(
        f"Written {args.out}: {len(snapshot.runs)} runs, {len(snapshot.questions)} questions, "
        f"{args.out.stat().st_size // 1024} kB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
