"""Run a golden set through QueryMind and measure execution accuracy.

    uv run python -m evals.run --suite golden                       # all 30 questions
    uv run python -m evals.run --suite golden --limit 5             # a quick check
    uv run python -m evals.run --suite golden --ids e01_customer_count m07_revenue_1997
    uv run python -m evals.run --suite golden --report docs/eval-reports/phase1-baseline.md
    uv run python -m evals.run --suite golden --max-attempts 1     # no self-correction (ablation)
    uv run python -m evals.run --suite business                     # questions in business terms
    uv run python -m evals.run --suite business --no-semantic-layer # same, without definitions
    uv run python -m evals.run --suite chinook                      # the music store (3.2)
    uv run python -m evals.run --suite chinook --schema-retrieval off  # whole schema (3.3)
    uv run python -m evals.run --suite bird --limit 20 --provider gemini   # BIRD benchmark (4.1)
    uv run python -m evals.run --suite bird --no-evidence           # without BIRD's hints
    uv run python -m evals.run --suite bird --references-only       # only the gold SQL, no LLM

For each question: QueryMind writes and runs its own SQL (the real pipeline, real LLM), the
reference SQL runs too, and the two RESULTS are compared (evals/compare.py). The score is the
share of questions whose results match. Uses real LLM calls: not run in CI.

The BIRD suite (4.1) spans 11 databases: each question runs in its own (bird_<db_id>), with one
Pipeline per database, BIRD's protocol (whole schema, no definitions of ours, evals/bird.py),
and the evidence hint after the question unless --no-evidence.
"""

import argparse
import logging
import subprocess
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from evals.bird import (
    SUBSET_FILE,
    BirdError,
    bird_settings,
    interleave,
    load_bird_questions,
    load_subset,
    to_golden,
)
from evals.compare import compare_results
from evals.golden_set import GOLDEN_DIR, GoldenQuestion, load_golden_set
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest, Refusal
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError, QueryError
from querymind.db.executor import QueryResult, execute_query
from querymind.db.schema import introspect_schema
from querymind.llm import (
    LLMError,
    LLMProviderError,
    create_embedding_provider,
    create_llm_provider,
)
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

SUITES = {
    "golden": GOLDEN_DIR / "northwind.yaml",
    "business": GOLDEN_DIR / "northwind_business.yaml",  # needs the semantic layer (3.1)
    "chinook": GOLDEN_DIR / "chinook.yaml",  # the music store, next to Northwind (3.2)
}
BIRD_SUITE = "bird"  # the BIRD Mini-Dev benchmark (4.1): 11 databases, not a YAML file
TIERS = ("easy", "medium", "hard")
CONFIDENCE_LEVELS = ("high", "medium", "low")

# ask() gets the whole item, not just its text: a BIRD question picks its database by its id.
type AskFn = Callable[[GoldenQuestion], AgentReply]
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
    confidence: str | None  # the answer's confidence level; None when there was no answer
    # False = no verdict: the LLM provider stayed unavailable (e.g. a daily rate limit), so the
    # agent never got to answer. Left out of the accuracy, and listed for a rerun instead.
    scored: bool = True
    # What the LLM was shown (3.3): "full", "retrieved" or "fallback"; tables; size in tokens.
    schema_mode: str | None = None
    schema_tables: int = 0
    schema_tokens: int = 0


@dataclass(frozen=True)
class AuditedAsk:
    """What the pipeline's last audit line said: the last SQL tried, how many queries, and
    the schema the LLM was shown."""

    sql: str | None = None
    sql_attempts: int = 0
    schema_mode: str | None = None
    schema_tables: int = 0
    schema_tokens: int = 0


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
            answer = ask(item)
        except (LLMProviderError, DatabaseUnavailableError) as exc:
            if attempt <= retries:
                sleep(backoff_seconds * attempt)
                continue
            return _failed(item, exc, started, attempt, last_audit(), scored=False)
        except (UnsafeSqlError, QueryError, LLMError, ValueError) as exc:
            return _failed(item, exc, started, attempt, last_audit())

        if not isinstance(answer, AskResult):
            return _with_schema(_no_answer(item, answer, started, attempt), last_audit())
        comparison = compare_results(
            expected=reference(item), actual=answer.result, ordered=item.ordered
        )
        outcome = Outcome(
            item=item,
            passed=comparison.match,
            strict=comparison.strict,
            reason=comparison.reason,
            error=None,
            sql=answer.sql,
            seconds=time.perf_counter() - started,
            provider_attempts=attempt,
            sql_attempts=answer.sql_attempts,
            confidence=answer.confidence.level,
        )
        return _with_schema(outcome, last_audit())
    raise AssertionError("unreachable: the loop always returns")


def _with_schema(outcome: Outcome, audited: AuditedAsk) -> Outcome:
    """Add what the audit line says the LLM was shown (3.3)."""
    return replace(
        outcome,
        schema_mode=audited.schema_mode,
        schema_tables=audited.schema_tables,
        schema_tokens=audited.schema_tokens,
    )


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
        confidence=None,
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
    outcome = Outcome(
        item=item,
        passed=False,
        strict=False,
        reason=message,
        error=type(exc).__name__,
        sql=audited.sql,
        seconds=time.perf_counter() - started,
        provider_attempts=attempt,
        sql_attempts=audited.sql_attempts,
        confidence=None,
        scored=scored,
    )
    return _with_schema(outcome, audited)


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
    # Calibration: accuracy among the answers at each confidence level. A useful score has
    # high >= medium >= low; answers that never came (errors, clarifications) aren't counted.
    by_confidence: dict[str, Score]
    seconds: float
    # The schema the LLM saw (3.3): mean size over the questions that got one, and the ids
    # whose retrieval failed and fell back to the whole schema (they don't count as retrieval).
    schema_tokens: float = 0.0
    schema_modes: dict[str, int] | None = None
    fallbacks: tuple[str, ...] = ()


def summarize_outcomes(outcomes: Sequence[Outcome]) -> Summary:
    def score(group: Iterable[Outcome], *, strict: bool = False) -> Score:
        group = [o for o in group if o.scored]  # unscored questions count neither way
        return Score(sum(o.strict if strict else o.passed for o in group), len(group))

    tags = sorted({tag for o in outcomes for tag in o.item.tags})
    shown = [o for o in outcomes if o.schema_mode]
    return Summary(
        accuracy=score(outcomes),
        strict=score(outcomes, strict=True),
        by_tier={tier: score(o for o in outcomes if o.item.tier == tier) for tier in TIERS},
        by_tag={tag: score(o for o in outcomes if tag in o.item.tags) for tag in tags},
        errors=dict(Counter(o.error for o in outcomes if o.error and o.scored)),
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
    )


def build_report(
    outcomes: Sequence[Outcome],
    *,
    title: str,
    meta: dict[str, str],
    run_flags: str = "--suite golden",
    reference_failures: dict[str, str] | None = None,
) -> str:
    """A markdown report: scores, breakdowns, then every failure with both SQLs.

    `reference_failures`: questions whose reference SQL itself failed (id → why). They were
    not asked, and are listed so they are never silently dropped.
    """
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
        f"| Schema shown (mean size) | {_modes(summary)}, ~{summary.schema_tokens:,.0f} tokens |",
        f"| Retrieval fell back to the whole schema | {len(summary.fallbacks)} |",
        f"| Total time | {summary.seconds:.0f} s |",
        "",
        "## By tier",
        "",
        "| Tier | Passed |",
        "|---|---|",
        *(f"| {tier} | {score} |" for tier, score in summary.by_tier.items()),
        "",
        "## By confidence (calibration)",
        "",
        "Accuracy among the answers given at each level. High should be the most accurate.",
        "",
        "| Confidence | Passed |",
        "|---|---|",
        *(f"| {level} | {score} |" for level, score in summary.by_confidence.items()),
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
        "| Question | Tier | Result | SQL tries | Confidence | Schema | Time | Why |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for o in outcomes:
        result = _status(o)
        if o.passed and not o.strict:
            result += " (extra columns)"
        why = o.reason.replace("|", "\\|").replace("\n", " ")
        schema = (
            f"{o.schema_mode}: {o.schema_tables} tables, ~{o.schema_tokens:,}"
            if o.schema_mode
            else "-"
        )
        lines.append(
            f"| `{o.item.id}` | {o.item.tier} | {result} | {o.sql_attempts or '-'} "
            f"| {o.confidence or '-'} | {schema} | {o.seconds:.1f} s | {why} |"
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


@dataclass(frozen=True)
class Suite:
    """The questions of one run, and the database each one is about."""

    name: str
    label: str  # for the report, e.g. "northwind, version 2"
    items: list[GoldenQuestion]
    databases: dict[str, str]  # question id → Postgres database; empty = the one in .env


def load_suite(name: str, *, full: bool = False, evidence: bool = True) -> Suite:
    """A golden YAML set, or BIRD: the fixed 100-question subset (all 500 with `full`).

    Raises:
        BirdError: BIRD isn't downloaded yet (data/seed_bird.py) or the subset file is invalid.
    """
    if name != BIRD_SUITE:
        golden = load_golden_set(SUITES[name])
        return Suite(name, f"{golden.database}, version {golden.version}", [*golden.questions], {})
    questions = load_bird_questions()
    chosen = interleave(questions if full else load_subset(questions))  # --limit: all databases
    source = "all 500" if full else f"the fixed subset, {SUBSET_FILE.name}"
    label = f"BIRD Mini-Dev, {source}, evidence {'on' if evidence else 'off'}"
    return Suite(
        name,
        label,
        [to_golden(q, evidence=evidence) for q in chosen],
        {q.id: q.database for q in chosen},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure QueryMind's execution accuracy.")
    parser.add_argument("--suite", choices=[*SUITES, BIRD_SUITE], default="golden")
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
    parser.add_argument(
        "--no-semantic-layer",
        action="store_true",
        help="leave the business definitions out of the prompt (ablation)",
    )
    parser.add_argument(
        "--schema-retrieval",
        choices=["auto", "on", "off"],
        help="override SCHEMA_RETRIEVAL (off = the whole schema, the 3.3 ablation)",
    )
    parser.add_argument(
        "--retrieval-mode",
        choices=["vector", "keyword", "hybrid", "hybrid_rerank"],
        help="override RETRIEVAL_MODE (how tables are ranked, 3.4)",
    )
    parser.add_argument(
        "--few-shot",
        choices=["on", "off"],
        help="off = no few-shot examples in the prompt (the 3.5 ablation); on = FEW_SHOT_EXAMPLES",
    )
    parser.add_argument(
        "--no-evidence", action="store_true", help="bird: ask without BIRD's hints (ablation)"
    )
    parser.add_argument(
        "--full", action="store_true", help="bird: all 500 questions, not the fixed 100"
    )
    parser.add_argument(
        "--references-only",
        action="store_true",
        help="only run the reference SQL (no LLM): checks the suite itself",
    )
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    try:
        suite = load_suite(args.suite, full=args.full, evidence=not args.no_evidence)
    except BirdError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    items = [q for q in suite.items if not args.ids or q.id in args.ids][: args.limit]
    if not items:
        print("Error: no questions match --ids / --limit", file=sys.stderr)
        return 1
    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        if args.max_attempts:
            settings = settings.model_copy(update={"max_sql_attempts": args.max_attempts})
        if args.no_semantic_layer:
            settings = settings.model_copy(update={"semantic_layer_paths": []})
        if args.schema_retrieval:
            settings = settings.model_copy(update={"schema_retrieval": args.schema_retrieval})
        if args.retrieval_mode:
            settings = settings.model_copy(update={"retrieval_mode": args.retrieval_mode})
        if args.few_shot == "off":
            settings = settings.model_copy(update={"few_shot_examples": 0})

        def settings_for(item: GoldenQuestion) -> Settings:
            database = suite.databases.get(item.id)
            return bird_settings(settings, database) if database else settings

        references, reference_failures = run_references(items, settings_for)
        if args.references_only:
            return _print_references(items, references, reference_failures)
        items = [item for item in items if item.id not in reference_failures]
        model, _ = settings.llm_credentials()
        pipelines = _pipelines(items, settings_for)
    except (ConfigError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1
    capture = AuditCapture.attach()

    def ask(item: GoldenQuestion) -> AgentReply:
        capture.last = AuditedAsk()
        pipeline = pipelines[settings_for(item).postgres_db]
        return pipeline.ask(item.question, summarize_answer=False)

    shown = settings_for(items[0])  # BIRD: what every one of its databases gets
    databases = f"{len(pipelines)} databases, " if len(pipelines) > 1 else ""
    print(
        f"Evaluating {len(items)} questions with {settings.llm_provider} / {model}, "
        f"{databases}max {shown.max_sql_attempts} SQL attempts, "
        f"semantic layer: {_layers(shown)}, schema retrieval: {shown.schema_retrieval}, "
        f"few-shot: {shown.few_shot_examples or 'off'}\n"
    )
    if reference_failures:
        print(f"Reference SQL failed, not asked: {', '.join(sorted(reference_failures))}\n")
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
    print(f"  schema shown: {_modes(summary)}, mean ~{summary.schema_tokens:,.0f} tokens")
    if summary.fallbacks:
        print(f"  retrieval fell back to the whole schema: {', '.join(summary.fallbacks)}")
    print("  confidence → accuracy: " + "   ".join(
        f"{level}: {score}" for level, score in summary.by_confidence.items()
    ))  # fmt: skip
    if summary.not_scored:
        print()
        print(f"Not scored (LLM or database unavailable): {', '.join(summary.not_scored)}")
        rerun = f"{rerun_flags(sys.argv[1:])} --ids {' '.join(summary.not_scored)}"
        print(f"Rerun: uv run python -m evals.run {rerun}")
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Suite": f"{suite.name} ({suite.label})",
            "Questions": str(len(items)),
            "LLM": f"{settings.llm_provider} / {model}",
            "Code": _git_commit(),
            "Max rows / timeout": f"{shown.max_rows} / {shown.query_timeout_seconds} s",
            "Max SQL attempts": str(shown.max_sql_attempts),
            "Semantic layer": _layers(shown),
            "Schema retrieval": f"{shown.schema_retrieval} ({shown.retrieval_mode}, "
            f"top-k {shown.retrieval_top_k}, max {shown.retrieval_max_tables} tables)",
            "Few-shot examples": str(shown.few_shot_examples or "off"),
        }
        report = build_report(
            outcomes,
            title=f"QueryMind eval: {suite.name} ({suite.label.split(',')[0]})",
            meta=meta,
            run_flags=rerun_flags(sys.argv[1:]),
            reference_failures=reference_failures,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(report, encoding="utf-8")
        print(f"\nReport written to {args.report}")
    return 0


def rerun_flags(argv: Sequence[str]) -> str:
    """This run's flags minus the ones that pick questions or outputs (--ids, --limit, --report).

    A rerun of the *not scored* questions must use the same setup (provider, ablation arm,
    evidence...), or its answers would be mixed into the wrong arm of a comparison.
    """
    kept: list[str] = []
    dropping = False
    for arg in argv:
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            dropping = name in {"--ids", "--limit", "--report"}
            if dropping and "=" in arg:
                dropping = False  # --limit=5: the value came with it
                continue
        if not dropping:
            kept.append(arg)
    return " ".join(kept)


def _pipelines(
    items: Sequence[GoldenQuestion], settings_for: Callable[[GoldenQuestion], Settings]
) -> dict[str, Pipeline]:
    """One Pipeline per database the questions use (BIRD: up to 11), keyed by database name.

    They share one LLM client. Embeddings only where schema retrieval may run: BIRD shows
    the whole schema and has no index, so its pipelines get none.
    """
    first = settings_for(items[0])
    llm = create_llm_provider(first)
    embeddings = create_embedding_provider(first)
    pipelines: dict[str, Pipeline] = {}
    for item in items:
        settings = settings_for(item)
        if settings.postgres_db not in pipelines:
            pipelines[settings.postgres_db] = Pipeline(
                llm=llm,
                settings=settings,
                embeddings=None if settings.schema_retrieval == "off" else embeddings,
            )
    return pipelines


def _print_references(
    items: Sequence[GoldenQuestion],
    references: dict[str, QueryResult],
    failures: dict[str, str],
) -> int:
    """--references-only: did every reference SQL run, and how big are the answers?"""
    truncated = sorted(qid for qid, result in references.items() if result.truncated)
    empty = sorted(qid for qid, result in references.items() if not result.rows)
    largest = max((r.row_count for r in references.values()), default=0)
    print(f"Reference SQL: {len(references)} of {len(items)} ran, {len(failures)} failed")
    print(f"  largest answer: {largest:,} rows; empty answers: {', '.join(empty) or 'none'}")
    if truncated:
        print(f"  cut off at the row cap (can't be compared fully): {', '.join(truncated)}")
    for qid, why in sorted(failures.items()):
        print(f"  FAILED {qid}: {why}")
    return 1 if failures or truncated else 0


def _modes(summary: Summary) -> str:
    """E.g. "retrieved 14, fallback 1": how many questions saw which kind of schema."""
    modes = summary.schema_modes or {}
    return ", ".join(f"{mode} {count}" for mode, count in sorted(modes.items())) or "-"


def _layers(settings: Settings) -> str:
    return ", ".join(p.name for p in settings.semantic_layer_paths) or "off"


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
            sql=getattr(record, "sql", None),
            sql_attempts=getattr(record, "sql_attempts", 0),
            schema_mode=getattr(record, "schema_mode", None),
            schema_tables=len(getattr(record, "schema_tables", None) or []),
            schema_tokens=getattr(record, "schema_tokens", None) or 0,
        )

    @classmethod
    def attach(cls) -> "AuditCapture":
        handler = cls()
        audit = logging.getLogger("querymind.audit")
        audit.addHandler(handler)
        audit.setLevel(logging.INFO)  # INFO lines are dropped by default (WARNING and up only)
        return handler


def run_references(
    items: Sequence[GoldenQuestion], settings_for: Callable[[GoldenQuestion], Settings]
) -> tuple[dict[str, QueryResult], dict[str, str]]:
    """Run every reference SQL once, through the same validator and executor as the agent.

    `settings_for` says which database (and row cap) each question uses. Returns the results,
    and the questions whose reference failed (id → why): those get no verdict instead of
    stopping the whole run, and the report lists them.
    """
    groups: dict[str, list[GoldenQuestion]] = defaultdict(list)
    for item in items:
        groups[settings_for(item).postgres_db].append(item)
    results: dict[str, QueryResult] = {}
    failures: dict[str, str] = {}
    for group in groups.values():
        settings = settings_for(group[0])
        with connect_readonly(settings) as conn:
            tables = introspect_schema(conn, sample_rows=0).table_names
            for item in group:
                try:
                    query = validate_sql(
                        item.sql, allowed_tables=tables, max_rows=settings.max_rows
                    )
                    results[item.id] = execute_query(
                        conn, query, timeout_seconds=settings.query_timeout_seconds
                    )
                except UnsafeSqlError as exc:
                    failures[item.id] = f"rejected by the validator: {exc.reason}"
                except QueryError as exc:
                    failures[item.id] = f"{type(exc).__name__}: {exc}"
    return results, failures


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
