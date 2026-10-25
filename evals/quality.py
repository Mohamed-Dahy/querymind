"""Answer quality (4.5): are the answers relevant and true to the data, and does retrieval show
the LLM the right tables? DeepEval and RAGAS, with a Gemini judge (flash-lite by default).

    uv run python -m evals.quality                # all stages; resumes from the saved JSON
    uv run python -m evals.quality --limit 5      # a quick check (first 5 questions)
    uv run python -m evals.quality --stages judge # only judge the answers already saved
    uv run python -m evals.quality --fresh        # start over, ignoring what is saved

Accuracy (evals/run.py) checks the SQL's rows. This checks the sentence the user reads, which a
second LLM call writes from those rows. Three stages, each saved to the JSON as it goes, so a
rate limit halfway loses nothing: run the same command again and it carries on.

1. **answers**: the agent (Groq) answers the 30 Northwind golden questions, summary on. Saved
   per question: the SQL and whether its rows matched the reference, the summary the LLM wrote
   (before grounding.py's number check, which may have replaced it) with that check's verdict,
   and the exact data the summarizer was shown.
2. **judge** (DeepEval): three metrics per LLM-written summary, scored 0-1 by the judge:
   AnswerRelevancy (does it answer the question?), Faithfulness (is every claim supported by
   the data?) and Hallucination (does it contradict the data? 1 = no). Compared with
   grounding.py, our free deterministic check, to see where the two disagree.
3. **retrieval** (RAGAS), on the 58 golden questions: ID-based context precision and recall,
   the tables retrieval chose against the tables the reference SQL reads (no LLM), and LLM
   context precision on a fixed sample of 5, to see whether the free ID version agrees.

The judge is a different provider than the agent (no model grades its own answers). A judge
call that fails leaves that metric *not scored*, never 0: a rate limit says nothing about the
answer. Uses real LLM calls: not run in CI.
"""

import argparse
import logging
import statistics
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from pydantic import BaseModel, Field

from evals.golden_set import GoldenQuestion, load_golden_set
from evals.ratelimit import FREE_TIER, Limits, RateLimitedLLM, RateLimiter
from evals.report import Outcome
from evals.retrieval import Retrieved, expected_tables, load_catalog
from evals.run import (
    SUITES,
    AuditCapture,
    AuditedAsk,
    PipelineAsk,
    ReferenceFn,
    SettingsFor,
    _git_commit,
    _limits_text,
    evaluate_question,
    run_references,
)
from querymind.agent.prompts.summarize import result_note
from querymind.agent.schemas import AgentReply, AskResult
from querymind.agent.summarizer import result_for_llm
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.llm import LLMProvider, LLMProviderError, create_embedding_provider
from querymind.llm.factory import create_llm_provider
from querymind.semantic.loader import SemanticLayerError
from querymind.semantic.retrieval import SchemaRetriever

REPORT = Path("docs/eval-reports/phase4-quality.md")
METRIC_NAMES = ("answer_relevancy", "faithfulness", "hallucination")  # judges.METRICS
QUALITY_FORMAT = 1  # bump when the JSON shape changes


class AnswerCase(BaseModel):
    """One golden question after stage 1: what the agent answered, and from which data."""

    id: str
    question: str
    sql: str | None = None
    sql_match: bool | None = None  # its rows matched the reference; None = not scored (outage)
    summary: str | None = None  # the LLM's own sentence, before the grounding check
    from_llm: bool = False  # the user was shown `summary` (True) or the safe fallback (False)
    ungrounded: list[str] = Field(default_factory=list)  # numbers grounding.py found nowhere
    context: str | None = None  # what the summarizer was shown: rows as JSON + how many
    skipped: str = ""  # why there is nothing to judge ("" = there is)


class Verdict(BaseModel):
    """One DeepEval metric on one answer."""

    metric: str
    score: float | None = None  # 0-1, higher is better; None = not scored (the judge failed)
    passed: bool | None = None  # score >= DeepEval's default threshold (0.5)
    reason: str = ""  # the judge's own explanation
    error: str | None = None  # the exception type, when the judge failed


class Judged(BaseModel):
    """Stage 2 for one answer: its verdicts, and what judging it cost."""

    id: str
    verdicts: list[Verdict]
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def get(self, metric: str) -> Verdict | None:
        return next((v for v in self.verdicts if v.metric == metric), None)


class RetrievalCase(BaseModel):
    """Stage 3 for one question: the tables it needs and the ones retrieval chose."""

    id: str
    question: str
    needed: list[str]  # from the reference SQL
    retrieved: list[str]  # in the order retrieval ranked them
    id_precision: float  # share of the retrieved tables that are needed
    id_recall: float  # share of the needed tables that were retrieved
    llm_precision: float | None = None  # RAGAS LLM context precision; None = not in the sample
    llm_error: str | None = None


class QualityRun(BaseModel):
    """Everything the three stages saved: the JSON file next to the report."""

    format: int = QUALITY_FORMAT
    meta: dict[str, str] = Field(default_factory=dict)
    answers: list[AnswerCase] = Field(default_factory=list)
    judged: list[Judged] = Field(default_factory=list)
    retrieval: list[RetrievalCase] = Field(default_factory=list)


# ---------------------------------------------------------------- stage 1: answers


class SummaryCapture(logging.Handler):
    """The summary the LLM wrote when grounding.py rejected it.

    The summarizer then shows a templated fallback instead, so the AskResult no longer has
    the LLM's sentence; its warning log line does (`llm_answer`, `ungrounded`).
    """

    LOGGER = "querymind.agent.summarizer"

    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.rejected: tuple[str, list[str]] | None = None

    def emit(self, record: logging.LogRecord) -> None:
        text = getattr(record, "llm_answer", None)
        if isinstance(text, str):
            self.rejected = (text, [str(n) for n in getattr(record, "ungrounded", [])])

    @classmethod
    def attach(cls) -> "SummaryCapture":
        handler = cls()
        logging.getLogger(cls.LOGGER).addHandler(handler)
        return handler


def answer_case(
    item: GoldenQuestion,
    outcome: Outcome,
    reply: AgentReply | None,
    rejected: tuple[str, list[str]] | None,
) -> AnswerCase:
    """What stage 1 saves for one question. `rejected` = the summary grounding.py replaced."""
    case = AnswerCase(
        id=item.id,
        question=item.question,
        sql=outcome.sql,
        sql_match=outcome.passed if outcome.scored else None,
    )
    if not isinstance(reply, AskResult):
        return case.model_copy(update={"skipped": outcome.reason or "no answer"})
    result = reply.result
    result_json, shown = result_for_llm(result)
    note = result_note(shown=shown, total=result.row_count, truncated=result.truncated)
    case = case.model_copy(update={"context": f"{result_json}\n{note}"})
    if reply.answer.from_llm:
        return case.model_copy(update={"summary": reply.answer.text, "from_llm": True})
    if rejected is not None:
        summary, ungrounded = rejected
        return case.model_copy(update={"summary": summary, "ungrounded": ungrounded})
    # No rows (nothing to explain, so no LLM call) or the summary call itself failed.
    return case.model_copy(update={"skipped": "no LLM summary: empty result or the LLM failed"})


def collect_answers(
    items: Sequence[GoldenQuestion],
    *,
    ask: Callable[[GoldenQuestion], AgentReply],
    reference: ReferenceFn,
    capture: AuditCapture,
    summaries: SummaryCapture,
    on_case: Callable[[AnswerCase], None],
    retries: int,
    backoff_seconds: float,
) -> None:
    """Stage 1: ask each question (evals/run.py's retries and *not scored* rules)."""
    replies: list[AgentReply] = []  # the current question's replies (one per provider try)

    def remembering(question: GoldenQuestion) -> AgentReply:
        replies.append(ask(question))
        return replies[-1]

    for number, item in enumerate(items, start=1):
        replies.clear()
        capture.last = AuditedAsk()
        summaries.rejected = None
        outcome = evaluate_question(
            item,
            ask=remembering,
            reference=reference,
            retries=retries,
            backoff_seconds=backoff_seconds,
            last_audit=lambda: capture.last,
        )
        case = answer_case(item, outcome, replies[-1] if replies else None, summaries.rejected)
        on_case(case)
        shown = "skipped: " + case.skipped if case.skipped else _shown(case)
        print(f"[{number:2}/{len(items)}] {item.id:30} SQL {_mark(case.sql_match)}  {shown}")


def _shown(case: AnswerCase) -> str:
    if case.from_llm:
        return "LLM summary shown"
    return f"grounding rejected it ({', '.join(case.ungrounded)} not in the data)"


# ---------------------------------------------------------------- stage 2: judge

type JudgeFn = Callable[[str, AnswerCase], tuple[float, bool, str]]


def judge_context(case: AnswerCase) -> str:
    """What an answer is checked against: what its writer saw, the question and the data.

    The question belongs in it. "408 orders were placed in 1997" takes 1997 from the question;
    a judge shown only the rows calls that a contradiction (Hallucination did, on e04 and e07).
    """
    return f"Question: {case.question}\n\n{case.context}"


def judge_case(
    case: AnswerCase,
    *,
    judge: JudgeFn,
    retries: int,
    backoff_seconds: float,
    metrics: Sequence[str] = METRIC_NAMES,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Verdict]:
    """`metrics` on one answer. Provider errors are retried, then left *not scored*.

    Any other exception (e.g. the judge's output didn't fit DeepEval's schema) is also *not
    scored*: a broken judge call is no verdict about the answer.
    """
    verdicts = []
    for metric in metrics:
        for attempt in range(retries + 1):
            try:
                score, passed, reason = judge(metric, case)
            except LLMProviderError as exc:
                if attempt < retries:
                    sleep(backoff_seconds * (attempt + 1))
                    continue
                verdicts.append(Verdict(metric=metric, error=type(exc).__name__))
            except Exception as exc:  # see the docstring: never a verdict about the answer
                verdicts.append(Verdict(metric=metric, error=type(exc).__name__, reason=str(exc)))
            else:
                verdicts.append(Verdict(metric=metric, score=score, passed=passed, reason=reason))
            break
    return verdicts


def judge_answers(
    cases: Sequence[AnswerCase],
    done: dict[str, Judged],
    *,
    judge: JudgeFn,
    capture: AuditCapture,
    on_judged: Callable[[Judged], None],
    retries: int,
    backoff_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Stage 2: judge every answer that has an LLM summary, on the metrics it has no score for.

    Saved scores are kept (a rerun only spends calls on what is missing). Returns False when the
    judge's provider stayed unavailable (a daily limit): it stops there, and the next run
    carries on from the saved verdicts.
    """

    def missing(case: AnswerCase) -> list[str]:
        saved = done.get(case.id)
        return [
            m for m in METRIC_NAMES if not (saved and (v := saved.get(m)) and v.score is not None)
        ]

    todo = [c for c in cases if c.summary and missing(c)]
    for number, case in enumerate(todo, start=1):
        capture.last = AuditedAsk()
        metrics = missing(case)
        new = judge_case(
            case,
            judge=judge,
            metrics=metrics,
            retries=retries,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
        saved = done.get(case.id)
        kept = [v for v in saved.verdicts if v.metric not in metrics] if saved else []
        verdicts = sorted([*kept, *new], key=lambda v: METRIC_NAMES.index(v.metric))
        judged = Judged(
            id=case.id,
            verdicts=verdicts,
            calls=(saved.calls if saved else 0) + capture.last.llm_calls,
            input_tokens=(saved.input_tokens if saved else 0) + capture.last.input_tokens,
            output_tokens=(saved.output_tokens if saved else 0) + capture.last.output_tokens,
        )
        on_judged(judged)
        scores = "  ".join(f"{v.metric} {_score(v)}" for v in verdicts)
        print(f"[{number:2}/{len(todo)}] {case.id:30} {scores}")
        if any(v.error == LLMProviderError.__name__ for v in verdicts):
            print("The judge's provider is unavailable: stopping. Run again later to carry on.")
            return False
    return True


# ---------------------------------------------------------------- stage 3: retrieval

type IdScoresFn = Callable[[list[str], list[str]], tuple[float, float]]
type PrecisionFn = Callable[[RetrievalCase, GoldenQuestion], float]


def retrieval_case(retrieved: Retrieved, id_scores: IdScoresFn) -> RetrievalCase:
    tables = list(retrieved.selection.tables)
    needed = sorted(retrieved.expected)
    precision, recall = id_scores(tables, needed)
    return RetrievalCase(
        id=retrieved.item.id,
        question=retrieved.item.question,
        needed=needed,
        retrieved=tables,
        id_precision=precision,
        id_recall=recall,
    )


def sample_ids(ids: Sequence[str], size: int) -> list[str]:
    """A fixed, evenly spread sample (every n-th question), the same on every run."""
    if size <= 0:
        return []
    step = max(1, len(ids) // size)
    return list(ids[::step][:size])


def add_llm_precision(
    cases: list[RetrievalCase],
    items: dict[str, GoldenQuestion],
    *,
    sample: Sequence[str],
    precision: PrecisionFn,
) -> list[RetrievalCase]:
    """LLM context precision for the sampled questions that don't have it yet."""
    out = []
    for case in cases:
        if case.id in sample and case.llm_precision is None:
            try:
                case = case.model_copy(update={"llm_precision": precision(case, items[case.id])})
            except Exception as exc:  # not scored, as in judge_case
                case = case.model_copy(update={"llm_error": type(exc).__name__})
            print(f"  LLM context precision {case.id}: {_number(case.llm_precision)}")
        out.append(case)
    return out


# ---------------------------------------------------------------- report


def build_report(run: QualityRun) -> str:
    """The Markdown report, from the saved JSON alone (so it can be rebuilt at any time)."""
    lines = ["# QueryMind eval: answer quality (DeepEval + RAGAS)", ""]
    lines += [f"- **{key}:** {value}" for key, value in run.meta.items()]
    if run.answers:
        lines += ["", *_answers_section(run)]
    if run.retrieval:
        lines += ["", *_retrieval_section(run.retrieval)]
    return "\n".join(lines) + "\n"


def _answers_section(run: QualityRun) -> list[str]:
    judged = {j.id: j for j in run.judged}
    with_summary = [c for c in run.answers if c.summary]
    lines = [
        "## Answers: DeepEval",
        "",
        f"{len(run.answers)} questions; {len(with_summary)} have an LLM-written summary to judge "
        f"(the others: no answer, an empty result, or the SQL failed). Scores are 0-1, higher "
        "is better; *pass* = at least 0.5 (DeepEval's default). Hallucination: 1 = the answer "
        "contradicts nothing in the data.",
        "",
        "| Metric | Asks | Mean | Pass | Not scored |",
        "|---|---|---|---|---|",
    ]
    asks = {
        "answer_relevancy": "does the answer address the question?",
        "faithfulness": "is every claim supported by the data it was given?",
        "hallucination": "does it contradict that data?",
    }
    for metric in METRIC_NAMES:
        verdicts = [v for j in judged.values() if (v := j.get(metric))]
        scored = [v for v in verdicts if v.score is not None]
        mean = statistics.mean(v.score for v in scored if v.score is not None) if scored else None
        passed = sum(bool(v.passed) for v in scored)
        lines.append(
            f"| {metric} | {asks[metric]} | {_number(mean)} | {passed}/{len(scored)} "
            f"| {len(with_summary) - len(scored)} |"  # incl. not judged yet
        )
    lines += ["", *_grounding_section(with_summary, judged)]
    wrong_sql = [c for c in with_summary if c.sql_match is False]
    if wrong_sql:
        lines += [
            "",
            "**Wrong SQL, judged answer.** These answers came from rows that did *not* match the "
            "reference. Faithfulness can only check the answer against the rows it got, so a "
            "faithful answer here is still a wrong answer: accuracy and faithfulness measure "
            "different things.",
            "",
            *[f"- `{c.id}`: faithfulness {_score(judged[c.id].get('faithfulness'))}"
              for c in wrong_sql if c.id in judged],
        ]  # fmt: skip
    lines += [
        "",
        "### Per question",
        "",
        "| Question | SQL match | Shown to the user | Relevancy | Faithfulness | Hallucination |",
        "|---|---|---|---|---|---|",
    ]
    for case in run.answers:
        verdict = judged.get(case.id)
        shown = case.skipped or ("LLM summary" if case.from_llm else "fallback (grounding)")
        scores = [_score(verdict.get(m) if verdict else None) for m in METRIC_NAMES]
        lines.append(f"| `{case.id}` | {_mark(case.sql_match)} | {shown} | {' | '.join(scores)} |")
    calls = sum(j.calls for j in judged.values())
    tokens_in = sum(j.input_tokens for j in judged.values())
    tokens_out = sum(j.output_tokens for j in judged.values())
    if judged:
        lines += [
            "",
            f"Judge cost: {calls} calls ({calls / len(judged):.1f} per answer), "
            f"{tokens_in:,} tokens in, {tokens_out:,} out.",
        ]
    return lines


def _grounding_section(cases: list[AnswerCase], judged: dict[str, Judged]) -> list[str]:
    """grounding.py (numbers only, free, on every answer) against the judge's Faithfulness."""
    pairs = [(c, v) for c in cases if c.id in judged and (v := judged[c.id].get("faithfulness"))]
    pairs = [(c, v) for c, v in pairs if v.passed is not None]
    agree = sum((not c.ungrounded) == v.passed for c, v in pairs)
    lines = [
        "### grounding.py vs the judge's Faithfulness",
        "",
        "grounding.py checks only numbers, with no LLM, before the user sees the answer; the "
        'judge reads every claim, after the fact. Both ask "is this supported by the data?".',
        "",
        f"They agree on {agree} of {len(pairs)} judged answers.",
    ]
    disagreements = [(c, v) for c, v in pairs if (not c.ungrounded) != v.passed]
    if disagreements:
        lines += ["", "| Question | grounding.py | Faithfulness | The judge's reason |",
                  "|---|---|---|---|"]  # fmt: skip
        for case, verdict in disagreements:
            grounding = (
                f"rejected ({', '.join(case.ungrounded)} not in the data)"
                if case.ungrounded
                else "passed"
            )
            reason = verdict.reason.replace("|", "/").replace("\n", " ")[:200]
            lines.append(f"| `{case.id}` | {grounding} | {_score(verdict)} | {reason} |")
    return lines


def _retrieval_section(cases: list[RetrievalCase]) -> list[str]:
    sampled = [c for c in cases if c.llm_precision is not None]
    complete = sum(c.id_recall == 1 for c in cases)
    lines = [
        "## Schema retrieval: RAGAS",
        "",
        'The "contexts" are tables: retrieval\'s choice for the question against the tables the '
        "reference SQL reads. Recall is the headline: a missed table is a query the LLM can't "
        "write. Precision is low by design (top-k plus join tables, a few extra tables cost only "
        "tokens).",
        "",
        "| Metric | Value | Questions |",
        "|---|---|---|",
        f"| ID-based context recall | {statistics.mean(c.id_recall for c in cases):.3f} "
        f"| {len(cases)} ({complete} with every table) |",
        f"| ID-based context precision | {statistics.mean(c.id_precision for c in cases):.3f} "
        f"| {len(cases)} |",
    ]
    if sampled:
        llm_mean = statistics.mean(c.llm_precision for c in sampled if c.llm_precision is not None)
        id_mean = statistics.mean(c.id_precision for c in sampled)
        lines += [
            f"| LLM context precision (judge) | {llm_mean:.3f} | {len(sampled)} (sample) |",
            f"| ID-based precision, same sample | {id_mean:.3f} | {len(sampled)} |",
            "",
            "LLM context precision asks the judge, table by table, whether it helped write the "
            "reference SQL (one call per table). It is *average precision*: 1.0 when the useful "
            "tables are ranked first, however many extra tables follow. So it measures the "
            "ranking, and ID-based precision the share of needed tables: the two numbers answer "
            "different questions.",
            "",
            "| Question | Retrieved | Needed | ID precision | LLM precision |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| `{c.id}` | {len(c.retrieved)} | {', '.join(c.needed)} | {c.id_precision:.2f} "
            f"| {_number(c.llm_precision)} |"
            for c in sampled
        ]
    misses = [c for c in cases if c.id_recall < 1]
    lines += ["", "**Missed tables:** " + (
        "none, every question got every table it needs." if not misses else "; ".join(
            f"`{c.id}` ({', '.join(sorted(set(c.needed) - set(c.retrieved)))})" for c in misses
        )
    )]  # fmt: skip
    return lines


def _mark(value: bool | None) -> str:
    return "not scored" if value is None else ("yes" if value else "**no**")


def _number(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def _score(verdict: Verdict | None) -> str:
    if verdict is None:
        return "-"
    if verdict.score is None:
        return f"not scored ({verdict.error})"
    return f"{verdict.score:.2f}" if verdict.passed else f"**{verdict.score:.2f}**"


def load_run(path: Path) -> QualityRun:
    """The saved JSON, or an empty run if there is none yet."""
    if not path.exists():
        return QualityRun()
    run = QualityRun.model_validate_json(path.read_text(encoding="utf-8"))
    if run.format != QUALITY_FORMAT:
        raise ValueError(f"{path}: format {run.format}, expected {QUALITY_FORMAT}: use --fresh")
    return run


def save_run(run: QualityRun, report: Path) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    report.with_suffix(".json").write_text(run.model_dump_json(indent=2), encoding="utf-8")
    report.write_text(build_report(run), encoding="utf-8")


# ---------------------------------------------------------------- main


def _rate_limited(llm: LLMProvider, limits: Limits, capture: AuditCapture) -> LLMProvider:
    """One process, one limiter: each call waits for room in the provider's per-minute limits."""
    return RateLimitedLLM(
        llm,
        RateLimiter(limits),
        limits=limits,
        used=lambda: (capture.last.input_tokens, capture.last.output_tokens),
    )


def _check_meta(run: QualityRun, key: str, value: str, *, has_results: bool) -> None:
    """Saved results and new ones must come from the same model (one model per column)."""
    saved = run.meta.get(key)
    if has_results and saved and saved != value:
        raise ConfigError(f"the saved run used {key} {saved}, this one {value}: use --fresh")
    run.meta[key] = value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Answer quality with DeepEval + RAGAS (4.5).")
    parser.add_argument(
        "--stages", nargs="+", choices=["answers", "judge", "retrieval"],
        default=["answers", "judge", "retrieval"],
    )  # fmt: skip
    parser.add_argument("--provider", choices=["groq", "gemini"], default="groq", help="agent")
    parser.add_argument("--judge-provider", choices=["gemini", "groq"], default="gemini")
    parser.add_argument("--judge-model", default="gemini-3.5-flash-lite", help="the model judging")
    parser.add_argument(
        "--judge-rpm", type=int, help="judge requests per minute (default: the free tier's)"
    )
    parser.add_argument("--limit", type=int, help="only the first N questions (each stage)")
    parser.add_argument(
        "--llm-sample", type=int, default=5, help="questions for LLM context precision"
    )
    parser.add_argument("--retries", type=int, default=2, help="retries after a rate limit")
    parser.add_argument("--backoff", type=float, default=60.0, help="seconds before retry 1")
    parser.add_argument("--fresh", action="store_true", help="ignore the saved JSON")
    parser.add_argument(
        "--rejudge", nargs="+", choices=METRIC_NAMES, default=[], help="judge these metrics again"
    )
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]  # Windows console
    try:
        import evals.judges  # noqa: F401  deepeval + ragas: only in the eval group
    except ModuleNotFoundError as exc:
        print(f"Error: {exc.name} is missing: uv sync --group eval", file=sys.stderr)
        return 1

    try:
        run = QualityRun() if args.fresh else load_run(args.report.with_suffix(".json"))
        base = get_settings()
        agent_settings = base.model_copy(update={"llm_provider": args.provider})
        agent = f"{args.provider} / {agent_settings.llm_credentials()[0]}"
        judge = f"{args.judge_provider} / {args.judge_model}"
        if agent == judge:
            raise ConfigError(f"{agent} would judge its own answers: pick another --judge-model")
        judge_settings = base.model_copy(
            update={
                "llm_provider": args.judge_provider,
                f"{args.judge_provider}_model": args.judge_model,
            }
        )
        capture = AuditCapture.attach()
        limits = FREE_TIER[args.judge_provider]
        if args.judge_rpm:
            limits = replace(limits, requests_per_minute=args.judge_rpm)
        judge_llm = _rate_limited(create_llm_provider(judge_settings), limits, capture)
        run.meta["Date"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        run.meta["Code"] = _git_commit()
        if "answers" in args.stages:
            _check_meta(run, "Agent LLM", agent, has_results=bool(run.answers))
        if "judge" in args.stages or ("retrieval" in args.stages and args.llm_sample > 0):
            judged = bool(run.judged) or any(c.llm_precision is not None for c in run.retrieval)
            _check_meta(run, "Judge LLM", judge, has_results=judged)
            run.meta["Judge pace"] = _limits_text(limits)
        if "answers" in args.stages:
            _stage_answers(run, agent_settings, capture, args)
        if "judge" in args.stages:
            _stage_judge(run, judge_llm, capture, args)
        if "retrieval" in args.stages:
            _stage_retrieval(run, judge_llm, agent_settings, args)
    except (ConfigError, SemanticLayerError, psycopg.Error, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1
    save_run(run, args.report)
    print(f"\nReport written to {args.report} and {args.report.with_suffix('.json')}")
    return 0


def _stage_answers(
    run: QualityRun, settings: Settings, capture: AuditCapture, args: argparse.Namespace
) -> None:
    questions = list(load_golden_set(SUITES["golden"]).questions)
    order = [item.id for item in questions]
    items = questions[: args.limit]
    # Asked again: questions with no saved answer, or none because of an outage.
    keep = {c.id: c for c in run.answers if c.sql_match is not None and c.id in order}
    todo = [item for item in items if item.id not in keep]
    print(f"Stage 1, answers: {len(todo)} to ask ({len(items) - len(todo)} saved), "
          f"{run.meta['Agent LLM']}")  # fmt: skip
    if not todo:
        return
    settings_for = SettingsFor(settings, {})
    references, failures = run_references(todo, settings_for)
    todo = [item for item in todo if item.id not in failures]
    limits = FREE_TIER[settings.llm_provider]
    ask = PipelineAsk(tuple(todo), settings_for, summarize_answer=True)(
        lambda llm: _rate_limited(llm, limits, capture)
    )

    def on_case(case: AnswerCase) -> None:
        keep[case.id] = case
        run.answers = sorted(keep.values(), key=lambda c: order.index(c.id))
        save_run(run, args.report)

    collect_answers(
        todo,
        ask=ask,
        reference=lambda item: references[item.id],
        capture=capture,
        summaries=SummaryCapture.attach(),
        on_case=on_case,
        retries=args.retries,
        backoff_seconds=args.backoff,
    )


def _stage_judge(
    run: QualityRun, llm: LLMProvider, capture: AuditCapture, args: argparse.Namespace
) -> None:
    from evals.judges import judge_answer

    # --rejudge: forget these metrics' saved verdicts (e.g. after a change to what they see).
    done = {
        j.id: j.model_copy(
            update={"verdicts": [v for v in j.verdicts if v.metric not in args.rejudge]}
        )
        for j in run.judged
    }
    order = [c.id for c in run.answers]

    def judge(metric: str, case: AnswerCase) -> tuple[float, bool, str]:
        assert case.summary is not None and case.context is not None
        return judge_answer(
            llm,
            args.judge_model,
            metric,
            question=case.question,
            answer=case.summary,
            context=judge_context(case),
        )

    def on_judged(judged: Judged) -> None:
        done[judged.id] = judged
        run.judged = sorted(done.values(), key=lambda j: order.index(j.id))
        save_run(run, args.report)

    print(f"\nStage 2, judge: DeepEval with {run.meta['Judge LLM']}, {run.meta['Judge pace']}")
    judge_answers(
        run.answers[: args.limit],
        done,
        judge=judge,
        capture=capture,
        on_judged=on_judged,
        retries=args.retries,
        backoff_seconds=args.backoff,
    )


def _stage_retrieval(
    run: QualityRun, llm: LLMProvider, settings: Settings, args: argparse.Namespace
) -> None:
    from evals.judges import context_precision, id_scores

    items = [q for suite in SUITES.values() for q in load_golden_set(suite).questions]
    items = items[: args.limit]
    by_id = {item.id: item for item in items}
    catalog, documents = load_catalog(settings)
    print(f"\nStage 3, retrieval: {len(items)} questions, {settings.retrieval_mode} "
          f"top-{settings.retrieval_top_k}")  # fmt: skip
    if not run.retrieval or {c.id for c in run.retrieval} != set(by_id):
        embeddings = create_embedding_provider(settings)
        if embeddings is None:
            raise ConfigError("GOOGLE_API_KEY is not set: retrieval needs embeddings")
        retriever = SchemaRetriever(
            catalog=catalog,
            embeddings=embeddings,
            connect=connect_readonly,
            settings=settings,
            documents=documents,
        )
        cases = []
        for item in items:
            selection = retriever.select(item.question).info
            retrieved = Retrieved(item, expected_tables(item, catalog.schema), selection, 0.0)
            cases.append(retrieval_case(retrieved, lambda r, n: id_scores(retrieved=r, needed=n)))
        run.retrieval = cases
        save_run(run, args.report)

    def precision(case: RetrievalCase, item: GoldenQuestion) -> float:
        contexts = [documents.get(table, table) for table in case.retrieved]
        return context_precision(llm, question=item.question, reference=item.sql, contexts=contexts)

    sample = sample_ids([c.id for c in run.retrieval], args.llm_sample)
    run.retrieval = add_llm_precision(run.retrieval, by_id, sample=sample, precision=precision)


if __name__ == "__main__":
    sys.exit(main())
