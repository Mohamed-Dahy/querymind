"""evals/snapshot.py: what goes in the committed results file, and what never does. No LLM or DB."""

import dataclasses
import json
from pathlib import Path

import pytest

from evals import ablation
from evals.models import MODELS, STUDY_SUITES, load_prices, report_path
from evals.quality import AnswerCase, Judged, QualityRun, RetrievalCase, Verdict
from evals.report import Outcome, load_report, write_reports
from evals.snapshot import (
    MAX_REASON,
    MILESTONES_FILE,
    SNAPSHOT_FILE,
    SnapshotError,
    build_snapshot,
    clean_reason,
    load_hand_written,
    main,
    render,
    suites_to_compare,
    write_snapshot,
)
from querymind.api.evals import EvalsReply, with_bird_text
from tests.unit.test_report import outcome

PLANTED = "Maria Anders planted value"
SETTINGS = {
    "Max SQL attempts": "3",
    "Semantic layer": "northwind.yaml, chinook.yaml",
    "Schema retrieval": "auto",
    "Few-shot examples": "3",
}
FLASH_LITE = "gemini / gemini-3.5-flash-lite"
REAL_REPORTS = Path("docs/eval-reports")
needs_real_reports = pytest.mark.skipif(
    not (REAL_REPORTS / "phase4-ablations.md").exists(),
    reason="the reports are git-ignored, so only a machine that ran the evals has them",
)


def save(directory: Path, stem: str, *outcomes: Outcome, llm: str, suite: str) -> None:
    meta = {
        "LLM": llm,
        "Suite": suite,
        "Date": "2026-10-24 19:45 UTC",
        "Code": "abc123",
        **SETTINGS,
    }
    write_reports(directory / f"{stem}.md", list(outcomes), title="t", meta=meta, max_rows=1000)


def golden_questions(suite: str, *, passed: bool = True) -> list[Outcome]:
    return [outcome(f"{suite}_q1", passed=passed, ex=passed, database=None)]  # type: ignore[arg-type]


def bird_questions() -> list[Outcome]:
    return [
        outcome("bird_1", tier="medium", passed=True, ex=False),
        outcome("bird_2", tier="hard", passed=False, ex=False),
    ]


def write_all_reports(directory: Path, *, models_on_chinook: bool = False) -> None:
    """Every report the snapshot reads, plus a trial run that it must ignore."""
    study = ablation.STUDIES["golden"]
    for arm in study.arms:
        for suite in study.suites:
            stem = ablation.report_path(study, arm, suite).stem
            # Arm A fails, so there is a gain to see.
            questions = golden_questions(suite, passed=arm.key != "A")
            save(directory, stem, *questions, llm=FLASH_LITE, suite=suite)
    for key in ("gpt-oss-20b", "qwen3.8-27b"):
        model = MODELS[key]
        suites = ("golden", "business", "chinook") if models_on_chinook else ("golden", "business")
        for suite in suites:
            stem = report_path(model, suite).stem
            save(directory, stem, *golden_questions(suite), llm=model.llm, suite=suite)
    for stem in ("phase4-bird-100-seq", "phase4-bird-100-w4"):
        save(directory, stem, *bird_questions(), llm=FLASH_LITE, suite="bird")
    trial = outcome("trial_only", passed=True, ex=True)
    save(directory, "phase4-bird-20", trial, llm=FLASH_LITE, suite="bird")


@pytest.fixture
def reports(tmp_path: Path) -> Path:
    write_all_reports(tmp_path)
    return tmp_path


def test_the_same_reports_give_the_same_bytes(reports: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_snapshot(build_snapshot(reports), first)
    write_snapshot(build_snapshot(reports), second)

    assert first.read_bytes() == second.read_bytes()
    assert b"\r\n" not in first.read_bytes()  # Windows mustn't make the same numbers look changed


def test_only_the_chosen_reports_go_in(reports: Path) -> None:
    snapshot = build_snapshot(reports)

    assert "phase4-bird-20" not in {run.key for run in snapshot.runs}
    assert "trial_only" not in {question.id for question in snapshot.questions}


def test_a_value_planted_anywhere_in_a_report_never_reaches_the_file(reports: Path) -> None:
    """The fields that hold data: rows, the answer, the judge's reason, notes, raw errors."""
    base = golden_questions("golden")[0]
    planted = dataclasses.replace(
        base,
        item=base.item.model_copy(update={"notes": PLANTED}),
        passed=False,
        error="LLMProviderError",
        reason=f"gemini request failed: {PLANTED}",
    )
    study = ablation.STUDIES["golden"]
    stem = ablation.report_path(study, study.arms[0], "golden").stem
    save(reports, stem, planted, llm=FLASH_LITE, suite="golden")
    quality = QualityRun(
        meta={"Agent LLM": "groq / x", "Judge LLM": "gemini / y", "Date": "d", "Code": "c"},
        answers=[
            AnswerCase(
                id="golden_q1",
                question="q?",
                summary=PLANTED,
                context=json.dumps({"rows": [[PLANTED]]}),
                ungrounded=[PLANTED],
                skipped=PLANTED,
            )
        ],
        judged=[
            Judged(
                id="golden_q1",
                verdicts=[Verdict(metric="faithfulness", score=0.0, passed=False, reason=PLANTED)],
            )
        ],
        retrieval=[
            RetrievalCase(
                id="golden_q1",
                question="q?",
                needed=["a"],
                retrieved=["a"],
                id_precision=1.0,
                id_recall=1.0,
                llm_error=PLANTED,
            )
        ],
    )
    (reports / "phase4-quality.json").write_text(quality.model_dump_json(), encoding="utf-8")

    text = render(build_snapshot(reports))

    assert PLANTED not in text
    assert "Maria" not in text


@pytest.mark.parametrize(
    ("error", "scored", "reason", "expected"),
    [
        (
            "LLMProviderError",
            False,
            "429 {'error': 'long vendor reply'}",
            "provider was unavailable",
        ),
        (
            "LLMOutputError",
            True,
            "Failed to parse from completion {raw}",
            "didn't match the schema",
        ),
        (None, False, "connection refused at some host", "database was unavailable"),
        (None, True, "expected 2 rows, got 0", "expected 2 rows, got 0"),
    ],
)
def test_reasons_are_safe_words(
    error: str | None, scored: bool, reason: str, expected: str
) -> None:
    case = outcome("q", passed=False, ex=False, error=error, scored=scored, reason=reason)

    assert expected in clean_reason(case)


def test_an_unexpected_long_reason_is_cut() -> None:
    case = outcome("q", passed=False, ex=False, reason="x" * 5000)

    assert len(clean_reason(case)) == MAX_REASON


def test_bird_keeps_its_verdicts_but_not_its_text(reports: Path) -> None:
    snapshot = build_snapshot(reports)
    questions = {question.id: question for question in snapshot.questions}

    assert questions["bird_1"].question is None
    assert questions["bird_1"].reference_sql is None
    assert questions["bird_1"].tier == "medium"
    assert questions["golden_q1"].question == "golden_q1?"
    assert questions["golden_q1"].reference_sql == "SELECT 1"
    bird_run = next(run for run in snapshot.runs if run.key == "phase4-bird-100-seq")
    assert [o.question_id for o in bird_run.outcomes] == ["bird_1", "bird_2"]
    assert bird_run.outcomes[0].sql == "SELECT 2"  # our own SQL is ours to keep
    assert snapshot.bird_text_available is False


def test_a_parallel_run_never_counts_for_latency(reports: Path) -> None:
    runs = {run.key: run for run in build_snapshot(reports).runs}

    assert runs["phase4-bird-100-w4"].latency_valid is False
    assert runs["phase4-bird-100-seq"].latency_valid is True


def test_the_ablation_table_comes_with_each_suite_and_its_gaps(reports: Path) -> None:
    studies = {study.name: study for study in build_snapshot(reports).ablations}

    golden = studies["golden"]
    assert [view.scope for view in golden.views] == ["all", "golden", "business", "chinook"]
    arms = {arm.key: arm for arm in golden.views[0].arms}
    assert arms["A"].match is not None
    assert (arms["A"].match.passed, arms["A"].match.total) == (0, 3)
    assert arms["B"].delta_points == 100.0
    assert golden.noise_questions == 2

    bird = {arm.key: arm for arm in studies["bird"].views[0].arms}
    assert bird["A"].match is None  # not run: empty, never a zero
    assert bird["A"].missing == ["bird"]
    assert bird["C"].ex is not None
    assert (bird["C"].ex.passed, bird["C"].ex.total) == (0, 2)


def test_an_arm_row_is_none_until_the_arm_has_run(reports: Path) -> None:
    study = ablation.STUDIES["bird"]
    results = ablation.collect(study, reports)

    rows = ablation.arm_rows(study, results)

    assert rows[0] is None
    assert rows[1] is None
    assert rows[2] is not None
    assert rows[2].delta is None  # nothing above it has run, so there's no change to report


def test_model_suites_come_from_the_data(tmp_path: Path) -> None:
    write_all_reports(tmp_path)
    assert suites_to_compare("golden", tmp_path) == ("golden", "business")
    assert suites_to_compare("bird", tmp_path) == ("bird",)


def test_the_so_far_only_note_shows_exactly_when_a_suite_is_missing(tmp_path: Path) -> None:
    partial, full = tmp_path / "partial", tmp_path / "full"
    partial.mkdir()
    full.mkdir()
    write_all_reports(partial)
    write_all_reports(full, models_on_chinook=True)

    def golden_study(directory: Path) -> object:
        return next(s for s in build_snapshot(directory).models.studies if s.name == "golden")

    with_gap = golden_study(partial)
    assert with_gap.partial_note == "so far only golden, business"  # type: ignore[attr-defined]
    assert with_gap.suites == ["golden", "business"]  # type: ignore[attr-defined]
    assert golden_study(full).partial_note is None  # type: ignore[attr-defined]


def test_a_study_with_one_model_says_so(reports: Path) -> None:
    bird = next(s for s in build_snapshot(reports).models.studies if s.name == "bird")

    assert bird.only_one_model is True
    assert [row.llm for row in bird.rows] == [FLASH_LITE]
    assert "groq / openai/gpt-oss-20b" in {gap.llm for gap in bird.not_run}


def test_every_model_row_costs_what_its_tokens_cost(reports: Path) -> None:
    golden = next(s for s in build_snapshot(reports).models.studies if s.name == "golden")
    prices = load_prices()

    for row in golden.rows:
        price = prices.models[row.llm]
        expected = (row.input_tokens * price.input + row.output_tokens * price.output) / 1e6
        assert row.cost_per_question == pytest.approx(expected)


def test_hand_written_points_stay_apart_from_phase_4s_computed_ones(reports: Path) -> None:
    milestones = build_snapshot(reports).milestones
    hand_written = load_hand_written().milestones

    assert milestones[: len(hand_written)] == hand_written
    assert [m.phase for m in milestones[len(hand_written) :]] == ["4", "4"]


def test_no_reports_is_an_error_and_keeps_the_file(tmp_path: Path) -> None:
    out = tmp_path / "snapshot.json"
    out.write_text("keep me", encoding="utf-8")

    with pytest.raises(SnapshotError):
        build_snapshot(tmp_path / "empty")
    code = main(["--reports", str(tmp_path / "empty"), "--out", str(out)])

    assert code == 1
    assert out.read_text(encoding="utf-8") == "keep me"


# --- The committed file: checked in CI, where the reports don't exist ---------------------------


def committed() -> EvalsReply:
    return EvalsReply.model_validate_json(SNAPSHOT_FILE.read_bytes())


def test_the_committed_file_fits_the_api_model() -> None:
    assert committed().runs


def test_the_committed_file_has_no_bird_text() -> None:
    bird = [question for question in committed().questions if question.study == "bird"]

    assert bird
    assert all(q.question is None and q.reference_sql is None for q in bird)


def test_the_committed_file_keeps_no_raw_provider_reply() -> None:
    text = SNAPSHOT_FILE.read_text(encoding="utf-8")

    assert "RESOURCE_EXHAUSTED" not in text
    assert "Failed to parse" not in text


def test_the_committed_file_is_small_enough_for_the_commit_hook() -> None:
    assert SNAPSHOT_FILE.stat().st_size < 500 * 1024


# --- Against the real reports: only where they exist --------------------------------------------


@needs_real_reports
def test_the_committed_file_equals_a_fresh_export() -> None:
    assert SNAPSHOT_FILE.read_text(encoding="utf-8") == render(build_snapshot(REAL_REPORTS))


@needs_real_reports
def test_every_hand_written_number_is_in_its_report() -> None:
    hand = load_hand_written(MILESTONES_FILE)
    for point in hand.milestones:
        report = (REAL_REPORTS / f"{point.report}.md").read_text(encoding="utf-8")
        assert f"{point.passed}/{point.total}" in report, point.report
    for speed_up in hand.speed_ups:
        report = (REAL_REPORTS / f"{speed_up.report}.md").read_text(encoding="utf-8")
        for number in (speed_up.before, speed_up.after, speed_up.factor):
            assert number.replace(" s", "") in report, number


@needs_real_reports
def test_the_api_shows_bird_as_the_agent_was_asked_it() -> None:
    """The downloaded text with its hint must equal what the saved run really asked."""
    bird_file = Path("data/raw/bird/mini_dev_postgresql.json")
    if not bird_file.exists():
        pytest.skip("BIRD's questions aren't downloaded here")
    reply = with_bird_text(committed(), bird_file)
    questions = {question.id: question for question in reply.questions}

    saved = load_report(REAL_REPORTS / "phase4-bird-100-seq.json")

    assert reply.bird_text_available is True
    for item in (o.item for o in saved.outcomes):
        assert questions[item.id].question == item.question
        assert (questions[item.id].reference_sql or "").strip() == item.sql.strip()


@needs_real_reports
def test_the_ablation_numbers_equal_the_markdown_table() -> None:
    table = (REAL_REPORTS / "phase4-ablations.md").read_text(encoding="utf-8")
    for study in build_snapshot(REAL_REPORTS).ablations:
        for arm in study.views[0].arms:
            if arm.match is None or arm.ex is None:
                continue
            match = f"{arm.match.passed}/{arm.match.total} "
            match += f"({100 * arm.match.passed / arm.match.total:.1f}%)"
            ex = f"{arm.ex.passed}/{arm.ex.total} ({100 * arm.ex.passed / arm.ex.total:.1f}%)"
            row = f"| {arm.key} | {arm.adds} | {match} | {ex} |"
            assert row in table
            assert (
                f"{arm.input_tokens:,} + {arm.output_tokens:,} | {arm.median_seconds:.1f} |"
                in table
            )
            if arm.delta_points is not None:
                assert f"{ex} | {arm.delta_points:+.1f} pts | {arm.not_scored} |" in table


@needs_real_reports
def test_the_model_numbers_equal_the_markdown_table() -> None:
    table = (REAL_REPORTS / "phase4-models.md").read_text(encoding="utf-8")
    assert "so far only golden, business" in table
    studies = {study.name: study for study in build_snapshot(REAL_REPORTS).models.studies}
    assert studies["golden"].partial_note == "so far only golden, business"
    assert set(STUDY_SUITES) == set(studies)
    for study in studies.values():
        assert f"**{study.questions} questions**" in table
        for row in study.rows:
            assert row.cost_per_question is not None
            assert row.cost_per_correct is not None
            line = (
                f"| {row.p50_seconds:.1f} | {row.p95_seconds:.1f} "
                f"| {row.input_tokens:,} + {row.output_tokens:,} "
                f"| ${row.cost_per_question:.5f} | ${row.cost_per_question * 1000:.2f} "
                f"| ${row.cost_per_correct:.5f} |"
            )
            assert line in table, row.llm
