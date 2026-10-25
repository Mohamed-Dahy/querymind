"""evals/models.py: commands, fairness checks, the common question set, cost. No LLM, no DB."""

import subprocess
import sys
from pathlib import Path

import pytest

from evals.ablation import STUDIES
from evals.models import (
    AGENT_FLAGS,
    MODELS,
    Model,
    ModelsError,
    Price,
    Prices,
    collect,
    command,
    common_ids,
    load_prices,
    percentile,
    row,
    run,
    table,
)
from evals.report import Outcome, write_reports
from tests.unit.test_report import outcome

SETTINGS = {
    "Suite": "golden (northwind, version 2)",
    "Max SQL attempts": "3",
    "Semantic layer": "northwind.yaml",
    "Schema retrieval": "auto",
    "Few-shot examples": "3",
}
SMALL = Model("small", "groq", "small-model")
LARGE = Model("large", "groq", "large-model")
PRICES = Prices.model_validate(
    {
        "checked": "2026-10-25",
        "models": {
            "groq / small-model": Price(input=0.10, output=0.50, source="x"),
            "groq / large-model": Price(input=1.00, output=5.00, source="x"),
        },
    }
)


def save(path: Path, *outcomes: Outcome, llm: str, **meta: str) -> None:
    write_reports(
        path, list(outcomes), title="t", meta={"LLM": llm, **SETTINGS, **meta}, max_rows=1000
    )


def save_all(tmp_path: Path, model: Model, *outcomes: Outcome, **meta: str) -> None:
    """The golden study's three suites; all the outcomes go in the first one."""
    for number, suite in enumerate(("golden", "business", "chinook")):
        path = tmp_path / f"phase4-models-{model.key}-{suite}.md"
        save(path, *(outcomes if number == 0 else ()), llm=model.llm, **meta)


def test_every_model_runs_the_full_agent_and_only_the_llm_changes(tmp_path: Path) -> None:
    assert STUDIES["golden"].arms[-1].flags == AGENT_FLAGS  # 4.4 arm D: the full agent
    assert command(SMALL, "golden", tmp_path) == [
        sys.executable,
        *("-m", "evals.run", "--suite", "golden"),
        *("--provider", "groq", "--model", "small-model", *AGENT_FLAGS),
        *("--delay", "30", "--report", str(tmp_path / "phase4-models-small-golden.md")),
    ]


def test_flash_lite_reuses_the_ablation_and_bird_runs() -> None:
    flash = MODELS["flash-lite"]
    assert flash.reuse["golden"] == "phase4-ablation-golden-D-golden"
    assert flash.reuse["bird"] == "phase4-bird-100-seq"


def test_run_keeps_saved_reports_and_never_reruns_a_reused_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[bytes]:
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    save(tmp_path / "phase4-models-small-golden.md", llm=SMALL.llm)
    reused = Model("old", "gemini", "m", reuse={"golden": "phase4-old"})

    assert run(SMALL, ["golden", "chinook"], force=False, report_dir=tmp_path) == 0
    assert run(reused, ["golden"], force=True, report_dir=tmp_path) == 0
    assert [cmd[cmd.index("--suite") + 1] for cmd in ran] == ["chinook"]


def test_a_report_from_another_model_is_refused(tmp_path: Path) -> None:
    # The 3.1 lesson: a .env change once ran a "Groq" arm on Gemini.
    save_all(tmp_path, SMALL, outcome("q1", passed=True, ex=True))
    save(tmp_path / "phase4-models-small-golden.md", llm="gemini / flash-lite")
    with pytest.raises(ModelsError, match="LLM is gemini / flash-lite"):
        collect("golden", tmp_path, (SMALL,))


def test_other_agent_settings_are_refused(tmp_path: Path) -> None:
    save_all(tmp_path, SMALL, outcome("q1", passed=True, ex=True))
    save_all(tmp_path, LARGE, outcome("q1", passed=True, ex=True), **{"Max SQL attempts": "1"})
    with pytest.raises(ModelsError, match="Max SQL attempts"):
        collect("golden", tmp_path, (SMALL, LARGE))


def test_rows_are_scored_on_the_questions_every_model_scored(tmp_path: Path) -> None:
    save_all(
        tmp_path,
        SMALL,
        outcome("q1", passed=True, ex=True),
        outcome("q2", passed=False, ex=False),
        outcome("q3", passed=True, ex=True),
    )
    save_all(
        tmp_path,
        LARGE,
        outcome("q1", passed=True, ex=True),
        outcome("q2", passed=True, ex=True),
        outcome("q3", passed=False, ex=False, scored=False),  # lost to a rate limit
    )
    runs = collect("golden", tmp_path, (SMALL, LARGE, Model("none", "groq", "n")))
    assert common_ids(runs) == {"q1", "q2"}  # "none" has no reports: not part of the set
    text = table("golden", runs, PRICES, tmp_path)
    assert "the same **2 questions**" in text
    rows = [line for line in text.splitlines() if line.startswith("| groq")]
    assert rows[0].startswith("| groq / large-model | 2/2 |")  # best first
    assert rows[1].startswith("| groq / small-model | 1/2 |")
    assert "groq / n: - (not run yet: golden, business, chinook)" in text


def test_cost_per_question_and_per_correct_answer(tmp_path: Path) -> None:
    save_all(
        tmp_path,
        SMALL,
        outcome("q1", passed=True, ex=True, input_tokens=4000, output_tokens=200),
        outcome("q2", passed=False, ex=False, input_tokens=2000, output_tokens=0),
    )
    (result,) = collect("golden", tmp_path, (SMALL,))
    r = row(result, {"q1", "q2"}, PRICES)
    # Mean 3,000 in at $0.10/M + 100 out at $0.50/M = $0.0003 + $0.00005.
    assert r.cost == pytest.approx(0.00035)
    assert r.cost_per_correct == pytest.approx(0.0007)  # 2 questions paid for, 1 right
    no_price = row(result, {"q1"}, Prices.model_validate({"checked": "2026-10-25", "models": {}}))
    assert no_price.cost is None and no_price.cost_per_correct is None


def test_percentile_is_nearest_rank() -> None:
    values = [float(n) for n in range(1, 21)]  # 1..20
    assert percentile(values, 0.95) == 19.0
    assert percentile(values, 0.5) == 10.0
    assert percentile([7.0], 0.95) == 7.0


def test_the_prices_file_covers_every_model() -> None:
    prices = load_prices()
    assert {m.llm for m in MODELS.values()} <= set(prices.models)
    assert all(p.source.startswith("https://") for p in prices.models.values())


def test_a_comparison_so_far_uses_only_the_suites_every_model_ran(tmp_path: Path) -> None:
    save(
        tmp_path / "phase4-models-small-golden.md",
        outcome("q1", passed=True, ex=True),
        llm=SMALL.llm,
    )
    runs = collect("golden", tmp_path, (SMALL,), suites=["golden"])
    assert runs[0].missing == []  # business and chinook aren't asked for
    text = table("golden", runs, PRICES, tmp_path, suites=["golden"])
    assert text.startswith(
        "## Golden 58 (Northwind 30 + business 13 + Chinook 15): so far only golden"
    )
