"""Run a golden set through QueryMind and measure execution accuracy.

    uv run python -m evals.run --suite golden                       # all 30 questions
    uv run python -m evals.run --suite golden --limit 5             # a quick check
    uv run python -m evals.run --suite golden --ids e01_customer_count m07_revenue_1997
    uv run python -m evals.run --suite golden --report docs/eval-reports/phase1-baseline.md
    uv run python -m evals.run --suite golden --max-attempts 1     # no self-correction (ablation)

For each question: QueryMind writes and runs its own SQL (the real pipeline, real LLM), the
reference SQL runs too, and the two RESULTS are compared (evals/compare.py). The score is the
share of questions whose results match. Uses real LLM calls: not run in CI.
"""

import argparse
import logging
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from evals.compare import compare_results
from evals.golden_set import GOLDEN_DIR, GoldenQuestion, load_golden_set
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest, Refusal
from querymind.config import ConfigError, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError, QueryError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import introspect_schema
from querymind.llm import LLMError, LLMProviderError, create_llm_provider
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

SUITES = {"golden": GOLDEN_DIR / "northwind.yaml"}
TIERS = ("easy", "medium", "hard")

type AskFn = Callable[[str], AgentReply]
type ReferenceFn = Callable[[GoldenQuestion], QueryResult]


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
    # False = no verdict: the LLM provider stayed unavailable (e.g. a daily rate limit), so the
    # agent never got to answer. Left out of the accuracy, and listed for a rerun instead.
    scored: bool = True


@dataclass(frozen=True)
class AuditedAsk:
    """What the pipeline's last audit line said: the last SQL tried, and how many queries."""

    sql: str | None = None
    sql_attempts: int = 0


def evaluate_question(
    item: GoldenQuestion,
    *,
    ask: AskFn,
    reference: ReferenceFn,
    retries: int = 2,
    backoff_seconds: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
    last_audit: Callable[[], AuditedAsk] = AuditedAsk,
) -> Outcome:
    """Ask one golden question and compare QueryMind's result with the reference result.

    An LLMProviderError (usually a rate limit) or a database that can't be reached is retried
    after a pause; if it never works, the question is *not scored*: an outage says nothing about
    the agent's accuracy. Every other failure (unsafe SQL, a query error, unusable LLM output)
    counts as a wrong answer, and so does asking back or refusing: every golden question is
    clear and only reads data. `last_audit` tells what a failed question tried (AuditCapture).
    """
    started = time.perf_counter()
    for attempt in range(1, retries + 2):
        try:
            answer = ask(item.question)
        except (LLMProviderError, DatabaseUnavailableError) as exc:
            if attempt <= retries:
                sleep(backoff_seconds * attempt)
                continue
            return _failed(item, exc, started, attempt, last_audit(), scored=False)
        except (UnsafeSqlError, QueryError, LLMError, ValueError) as exc:
            return _failed(item, exc, started, attempt, last_audit())

        if not isinstance(answer, AskResult):
            return _no_answer(item, answer, started, attempt)
        comparison = compare_results(
            expected=reference(item), actual=answer.result, ordered=item.ordered
        )
        return Outcome(
            item=item,
            passed=comparison.match,
            strict=comparison.strict,
            reason=comparison.reason,
            error=None,
            sql=answer.sql,
            seconds=time.perf_counter() - started,
            provider_attempts=attempt,
            sql_attempts=answer.sql_attempts,
        )
    raise AssertionError("unreachable: the loop always returns")


def _no_answer(
    item: GoldenQuestion, reply: ClarificationRequest | Refusal, started: float, attempt: int
) -> Outcome:
    """A clarifying question or a refusal instead of an answer: wrong for a golden question."""
    if isinstance(reply, ClarificationRequest):
        reason = f"asked back instead of answering: {reply.clarification.question}"
    else:
        reason = f"refused: {reply.message}"
    return Outcome(
        item=item,
        passed=False,
        strict=False,
        reason=reason,
        error=None,
        sql=None,
        seconds=time.perf_counter() - started,
        provider_attempts=attempt,
        sql_attempts=0,
    )


def _failed(
    item: GoldenQuestion,
    exc: Exception,
    started: float,
    attempt: int,
    audited: AuditedAsk,
    *,
    scored: bool = True,
) -> Outcome:
    message = exc.reason if isinstance(exc, UnsafeSqlError) else str(exc)
    return Outcome(
        item=item,
        passed=False,
        strict=False,
        reason=message,
        error=type(exc).__name__,
        sql=audited.sql,
        seconds=time.perf_counter() - started,
        provider_attempts=attempt,
        sql_attempts=audited.sql_attempts,
        scored=scored,
    )


def evaluate(
    items: Sequence[GoldenQuestion],
    *,
    ask: AskFn,
    reference: ReferenceFn,
    delay_seconds: float = 0.0,
    retries: int = 2,
    backoff_seconds: float = 30.0,
    on_outcome: Callable[[int, Outcome], None] = lambda _number, _outcome: None,
    sleep: Callable[[float], None] = time.sleep,
    last_audit: Callable[[], AuditedAsk] = AuditedAsk,
) -> list[Outcome]:
    """Run every question in order. `delay_seconds` spaces calls out to respect rate limits."""
    outcomes = []
    for number, item in enumerate(items, start=1):
        if number > 1 and delay_seconds:
            sleep(delay_seconds)
        outcome = evaluate_question(
            item,
            ask=ask,
            reference=reference,
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
            last_audit=last_audit,
        )
        outcomes.append(outcome)
        on_outcome(number, outcome)
    return outcomes


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
    accuracy: Score  # extra columns allowed: the headline number
    strict: Score
    by_tier: dict[str, Score]
    by_tag: dict[str, Score]
    errors: dict[str, int]  # exception type → how many scored questions failed with it
    not_scored: list[str]  # ids of questions with no verdict (LLM or database unavailable)
    corrected: list[str]  # ids that passed only after the agent fixed its own SQL
    seconds: float


def summarize_outcomes(outcomes: Sequence[Outcome]) -> Summary:
    def score(group: Iterable[Outcome], *, strict: bool = False) -> Score:
        group = [o for o in group if o.scored]  # unscored questions count neither way
        return Score(sum(o.strict if strict else o.passed for o in group), len(group))

    tags = sorted({tag for o in outcomes for tag in o.item.tags})
    return Summary(
        accuracy=score(outcomes),
        strict=score(outcomes, strict=True),
        by_tier={tier: score(o for o in outcomes if o.item.tier == tier) for tier in TIERS},
        by_tag={tag: score(o for o in outcomes if tag in o.item.tags) for tag in tags},
        errors=dict(Counter(o.error for o in outcomes if o.error and o.scored)),
        not_scored=[o.item.id for o in outcomes if not o.scored],
        corrected=[o.item.id for o in outcomes if o.passed and o.sql_attempts > 1],
        seconds=sum(o.seconds for o in outcomes),
    )


def build_report(outcomes: Sequence[Outcome], *, title: str, meta: dict[str, str]) -> str:
    """A markdown report: scores, breakdowns, then every failure with both SQLs."""
    summary = summarize_outcomes(outcomes)
    lines = [f"# {title}", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += [
        "",
        "## Score",
        "",
        "| Measure | Result |",
        "|---|---|",
        f"| **Execution accuracy** (extra columns allowed) | **{summary.accuracy}** |",
        f"| Strict (exactly the asked-for columns) | {summary.strict} |",
        f"| Passed after self-correction (SQL retried) | {len(summary.corrected)} |",
        f"| Not scored (LLM or database unavailable) | {len(summary.not_scored)} |",
        f"| Total time | {summary.seconds:.0f} s |",
        "",
        "## By tier",
        "",
        "| Tier | Passed |",
        "|---|---|",
        *(f"| {tier} | {score} |" for tier, score in summary.by_tier.items()),
        "",
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
            f"uv run python -m evals.run --suite golden --ids {' '.join(summary.not_scored)}",
            "```",
            "",
        ]
    if summary.errors:
        lines += ["## Errors (no answer at all)", "", "| Error | Questions |", "|---|---|"]
        lines += [f"| {name} | {count} |" for name, count in sorted(summary.errors.items())]
        lines.append("")

    lines += [
        "## All questions",
        "",
        "| Question | Tier | Result | SQL tries | Time | Why |",
        "|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        result = _status(o)
        if o.passed and not o.strict:
            result += " (extra columns)"
        why = o.reason.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| `{o.item.id}` | {o.item.tier} | {result} | {o.sql_attempts or '-'} "
            f"| {o.seconds:.1f} s | {why} |"
        )

    failures = [o for o in outcomes if o.scored and not o.passed]
    if failures:
        lines += ["", "## Failures in detail"]
    for o in failures:
        lines += [
            "",
            f"### `{o.item.id}` ({o.item.tier}): {o.error or 'wrong result'}",
            "",
            f"**Question:** {o.item.question}",
            "",
            f"**Why:** {o.reason}",
            "",
            "**QueryMind's SQL:**"
            if o.sql
            else "**QueryMind's SQL:** none (failed before running)",
        ]
        if o.sql:
            lines += ["", "```sql", o.sql, "```"]
        lines += ["", "**Reference SQL:**", "", "```sql", o.item.sql.strip(), "```"]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure QueryMind's execution accuracy.")
    parser.add_argument("--suite", choices=SUITES, default="golden")
    parser.add_argument("--limit", type=int, help="only the first N questions")
    parser.add_argument("--ids", nargs="+", help="only these question ids")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="override LLM_PROVIDER")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between questions")
    parser.add_argument("--retries", type=int, default=2, help="retries after a rate limit")
    parser.add_argument(
        "--backoff", type=float, default=60.0, help="seconds to wait before retry 1 (x2, x3...)"
    )
    parser.add_argument(
        "--max-attempts", type=int, help="override MAX_SQL_ATTEMPTS (1 = no self-correction)"
    )
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    golden = load_golden_set(SUITES[args.suite])
    items = [q for q in golden.questions if not args.ids or q.id in args.ids][: args.limit]
    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        if args.max_attempts:
            settings = settings.model_copy(update={"max_sql_attempts": args.max_attempts})
        model, _ = settings.llm_credentials()
        references = run_references(items)
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1
    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)
    capture = AuditCapture.attach()

    def ask(question: str) -> AgentReply:
        capture.last = AuditedAsk()
        return pipeline.ask(question, summarize_answer=False)

    print(
        f"Evaluating {len(items)} questions with {settings.llm_provider} / {model}, "
        f"max {settings.max_sql_attempts} SQL attempts\n"
    )
    outcomes = evaluate(
        items,
        ask=ask,
        last_audit=lambda: capture.last,
        reference=lambda item: references[item.id],
        delay_seconds=args.delay,
        retries=args.retries,
        backoff_seconds=args.backoff,
        on_outcome=lambda number, o: print(_progress_line(number, len(items), o)),
    )

    summary = summarize_outcomes(outcomes)
    print(f"\nExecution accuracy: {summary.accuracy}   strict: {summary.strict}")
    if summary.corrected:
        print(f"Passed after self-correction: {', '.join(summary.corrected)}")
    print("  " + "   ".join(f"{tier}: {score}" for tier, score in summary.by_tier.items()))
    if summary.not_scored:
        print()
        print(f"Not scored (LLM or database unavailable): {', '.join(summary.not_scored)}")
        print(f"Rerun: uv run python -m evals.run --ids {' '.join(summary.not_scored)}")
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Suite": f"{args.suite} ({golden.database}, version {golden.version})",
            "Questions": str(len(items)),
            "LLM": f"{settings.llm_provider} / {model}",
            "Code": _git_commit(),
            "Max rows / timeout": f"{settings.max_rows} / {settings.query_timeout_seconds} s",
            "Max SQL attempts": str(settings.max_sql_attempts),
        }
        title = f"QueryMind eval: {args.suite} ({golden.database})"
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(build_report(outcomes, title=title, meta=meta), encoding="utf-8")
        print(f"\nReport written to {args.report}")
    return 0


class AuditCapture(logging.Handler):
    """Remembers the pipeline's last audit line: the SQL tried and the number of queries.

    When a question fails (unsafe SQL, a database error), the pipeline raises, so the runner
    never sees the SQL. But the audit line always records it, so the report can still show
    exactly what QueryMind tried, and how many times.
    """

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.last = AuditedAsk()

    def emit(self, record: logging.LogRecord) -> None:
        self.last = AuditedAsk(
            sql=getattr(record, "sql", None), sql_attempts=getattr(record, "sql_attempts", 0)
        )

    @classmethod
    def attach(cls) -> "AuditCapture":
        handler = cls()
        audit = logging.getLogger("querymind.audit")
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)  # INFO lines are dropped by default (WARNING and up only)
        return handler


def run_references(items: Sequence[GoldenQuestion]) -> dict[str, QueryResult]:
    """Run every reference SQL once, through the same validator and executor as the agent."""
    settings = get_settings()
    with connect_readonly(settings) as conn:
        tables = introspect_schema(conn).table_names
        results = {}
        for item in items:
            query = validate_sql(item.sql, allowed_tables=tables, max_rows=settings.max_rows)
            results[item.id] = execute_query(
                conn, query, timeout_seconds=settings.query_timeout_seconds
            )
    return results


def _progress_line(number: int, total: int, outcome: Outcome) -> str:
    status = f"{_status(outcome):5}"
    detail = "" if outcome.passed else f"  {outcome.error or ''} {outcome.reason}".rstrip()
    notes = []
    if outcome.sql_attempts > 1:
        notes.append(f"{outcome.sql_attempts} SQL tries")
    if outcome.provider_attempts > 1:
        notes.append(f"provider attempt {outcome.provider_attempts}")
    noted = f" ({', '.join(notes)})" if notes else ""
    return (
        f"[{number:2}/{total}] {status} {outcome.item.id:30} "
        f"{outcome.seconds:5.1f}s{noted}{detail[:120]}"
    )


def _status(outcome: Outcome) -> str:
    if not outcome.scored:
        return "SKIP"
    if outcome.passed:
        return "PASS"
    return "ERROR" if outcome.error else "FAIL"


def _git_commit() -> str:
    """The commit the code was at, plus '(uncommitted changes)' if the tree was dirty."""
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return f"{commit} (uncommitted changes)" if dirty else commit


if __name__ == "__main__":
    sys.exit(main())
