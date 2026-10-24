"""Score a saved eval run again, without calling the LLM: re-run its SQL and compare again.

    uv run python -m evals.rescore docs/eval-reports/phase4-bird-20.json
    uv run python -m evals.rescore docs/eval-reports/phase4-bird-20.json --report <new>.md
    uv run python -m evals.rescore docs/eval-reports/<run>.json --workers 4   # Ray (4.3)

Why: the LLM part of a run is the slow and costly part (tokens, rate limits). The JSON report
keeps the SQL QueryMind wrote for every question, so when the *scoring* changes (a fix in
evals/compare.py, BIRD's official EX added next to our match), old runs can be scored again
for free and in seconds, and two scoring rules can be compared on the very same answers.

What is re-run: for each question QueryMind answered, its final SQL and the reference SQL, both
through the same validator and executor as in the run, in the same database and with the same
row cap. Questions without an answer (not scored, asked back, an error) keep their verdict:
there is nothing new to compare. With --workers N (needs `uv sync --group eval`), N Ray workers
re-run questions at the same time (evals/parallel.py): Postgres runs their queries in parallel.
"""

import argparse
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import psycopg

from evals.compare import bird_ex, compare_results
from evals.report import Outcome, SavedReport, load_report, summarize_outcomes, write_reports
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError, QueryError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import introspect_schema
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql


def rescore(saved: SavedReport, settings: Settings) -> list[Outcome]:
    """Every outcome of `saved`, scored again against fresh query results (no LLM)."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, outcome in enumerate(saved.outcomes):
        if _answered(outcome):
            groups[outcome.database or settings.postgres_db].append(index)
    outcomes = list(saved.outcomes)
    for database, indexes in groups.items():
        db_settings = database_settings(settings, database, saved.max_rows)
        with connect_readonly(db_settings) as conn:
            tables = introspect_schema(conn, sample_rows=0).table_names
            for index in indexes:
                outcomes[index] = rescore_outcome(
                    outcomes[index], conn=conn, tables=tables, settings=db_settings
                )
    return outcomes


def database_settings(settings: Settings, database: str, max_rows: int) -> Settings:
    """The settings a saved question ran with: its own database and the run's row cap."""
    return settings.model_copy(update={"postgres_db": database, "max_rows": max_rows})


def rescore_outcome(
    outcome: Outcome, *, conn: psycopg.Connection, tables: frozenset[str], settings: Settings
) -> Outcome:
    """One answered question: run both SQLs again and apply today's comparison rules."""

    def run(sql: str) -> QueryResult:
        query = validate_sql(sql, allowed_tables=tables, max_rows=settings.max_rows)
        return execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds)

    assert outcome.sql is not None  # only answered questions get here
    try:
        expected = run(outcome.item.sql)
        actual = run(outcome.sql)
    except (UnsafeSqlError, QueryError) as exc:
        # The same SQL ran before. Failing now means the database changed: say so, loudly.
        why = exc.reason if isinstance(exc, UnsafeSqlError) else str(exc)
        return replace(outcome, passed=False, strict=False, ex=False, reason=f"rescore: {why}")
    match = compare_results(expected=expected, actual=actual, ordered=outcome.item.ordered)
    official = bird_ex(expected=expected, actual=actual)
    return replace(
        outcome,
        passed=match.match,
        strict=match.strict,
        reason=match.reason,
        ex=official.match,
        ex_reason=official.reason,
    )


def _answered(outcome: Outcome) -> bool:
    return outcome.scored and outcome.error is None and outcome.sql is not None


def changes(before: Sequence[Outcome], after: Sequence[Outcome]) -> list[str]:
    """One line per question whose verdict changed, e.g. "bird_532: EX FAIL → PASS"."""
    lines = []
    for old, new in zip(before, after, strict=True):
        moved = [
            f"{label} {_word(getattr(old, field))} → {_word(getattr(new, field))}"
            for label, field in (("match", "passed"), ("EX", "ex"))
            if getattr(old, field) != getattr(new, field)
        ]
        if moved:
            lines.append(f"{old.item.id}: {', '.join(moved)}")
    return lines


def _rescore_with_ray(
    saved: SavedReport, settings: Settings, workers: int
) -> tuple[list[Outcome], float]:
    """Like `rescore`, with the answered questions spread over Ray workers. Also returns how
    long Ray took to start, which the timing leaves out (it's the same for any run size)."""
    from evals.parallel import ray_running, rescore_parallel  # Ray is optional (eval group)

    indexes = [i for i, outcome in enumerate(saved.outcomes) if _answered(outcome)]
    with ray_running(workers) as startup:
        redone = rescore_parallel(
            [saved.outcomes[i] for i in indexes],
            settings,
            max_rows=saved.max_rows,
            workers=workers,
        )
    outcomes = list(saved.outcomes)
    for index, outcome in zip(indexes, redone, strict=True):
        outcomes[index] = outcome
    return outcomes, startup


def _word(value: bool) -> str:
    return "PASS" if value else "FAIL"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score a saved eval run again (no LLM calls).")
    parser.add_argument("report", type=Path, help="a .json report written by evals.run --report")
    parser.add_argument("--report", dest="out", type=Path, help="write the rescored report here")
    parser.add_argument(
        "--workers", type=int, default=1, help="questions at the same time (Ray, 4.3); 1 = in turn"
    )
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # Windows console
    try:
        saved = load_report(args.report)
        settings = get_settings()
        started = time.perf_counter()
        if args.workers > 1:
            outcomes, startup = _rescore_with_ray(saved, settings, args.workers)
        else:
            outcomes, startup = rescore(saved, settings), 0.0
        seconds = time.perf_counter() - started - startup
    except ModuleNotFoundError as exc:
        if exc.name != "ray":
            raise
        print("Error: --workers needs Ray: uv sync --group eval", file=sys.stderr)
        return 1
    except (OSError, ValueError, ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        return 1

    before, after = summarize_outcomes(saved.outcomes), summarize_outcomes(outcomes)
    answered = sum(_answered(o) for o in outcomes)
    workers = (
        f", {args.workers} Ray workers (+{startup:.1f} s to start Ray)" if args.workers > 1 else ""
    )
    print(f"Re-ran {answered} answered questions in {seconds:.1f} s{workers} (no LLM calls).")
    print(f"QueryMind match: {before.accuracy} → {after.accuracy}")
    print(f"BIRD EX:         {before.ex} → {after.ex}")
    moved = changes(saved.outcomes, outcomes)
    print("Changed verdicts: " + (", ".join(moved) if moved else "none"))
    if args.out:
        meta = {**saved.meta, "Rescored from": str(args.report)}
        json_path = write_reports(
            args.out,
            outcomes,
            title=f"Rescored: {args.report.stem}",
            meta=meta,
            max_rows=saved.max_rows,
            reference_failures=saved.reference_failures,
            tier_names=saved.tier_names,
        )
        print(f"Report written to {args.out} and {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
