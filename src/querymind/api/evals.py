"""The endpoint for the evals lab: the saved eval results, read from one committed file.

`evals/snapshot.py` writes `evals/snapshot.json` from the saved reports. Those are git-ignored, so
without this file a fresh clone or a deployed app would have no numbers. This needs no database and
no LLM: it still answers with both down.

BIRD's question text is CC BY-SA data, so the file holds only BIRD's ids. Where the downloaded
questions file exists (data/seed_bird.py), this adds the text when it reads the snapshot.
"""

import logging
import threading
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from querymind.api.schemas import ErrorResponse
from querymind.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["evals"])

SNAPSHOT_FORMAT = 1  # bump when the file's shape changes


class EvalsUnavailableError(Exception):
    """The snapshot file is missing or can't be read."""


class EvalScore(BaseModel):
    passed: int
    total: int


class EvalBreakdown(BaseModel):
    """One tier's or one database's two scores."""

    name: str
    match: EvalScore
    ex: EvalScore


class EvalQuestion(BaseModel):
    id: str
    study: Literal["golden", "bird"]
    suite: str
    tier: str
    database: str | None
    # None for BIRD unless the downloaded file is there (see the module docstring).
    question: str | None
    reference_sql: str | None


class EvalOutcome(BaseModel):
    """How one run did on one question. Never the rows it got."""

    question_id: str
    scored: bool  # False = no verdict: the provider or the database stayed unavailable
    passed: bool  # QueryMind match
    ex: bool  # BIRD's official EX
    reason: str  # why it failed; short, see evals/snapshot.py clean_reason
    ex_reason: str
    sql: str | None
    sql_attempts: int
    seconds: float
    input_tokens: int
    output_tokens: int


class EvalRun(BaseModel):
    """One saved report: one suite on one setup."""

    key: str  # the report's name, without its extension
    label: str  # the column in the question matrix, e.g. "Arm A", "gpt-oss-20b"
    study: Literal["golden", "bird"]
    suite: str
    llm: str
    date: str
    code: str
    wall_time: str | None
    latency_valid: bool  # False: its times include waiting for the rate limiter
    match: EvalScore
    ex: EvalScore
    not_scored: list[str]
    by_tier: list[EvalBreakdown]
    by_database: list[EvalBreakdown]
    outcomes: list[EvalOutcome]


class AblationArm(BaseModel):
    """One row of the ablation table. The numbers are None when the arm hasn't run."""

    key: str
    adds: str
    match: EvalScore | None
    ex: EvalScore | None
    delta_points: float | None  # the headline against the row above, as the report has it
    not_scored: int
    repairs_per_question: float | None
    input_tokens: int | None  # mean per question
    output_tokens: int | None
    median_seconds: float | None
    reports: list[str]
    missing: list[str]  # suites without a report yet


class AblationView(BaseModel):
    """The same arms over all of a study's suites ("all"), or over one suite."""

    scope: str
    arms: list[AblationArm]


class AblationStudy(BaseModel):
    name: str
    title: str
    headline: Literal["match", "ex"]
    llm: str | None
    noise_questions: int  # two runs of one setup differ by about this many questions
    views: list[AblationView]


class ModelRow(BaseModel):
    llm: str
    questions: int
    passed: int
    ex: int
    p50_seconds: float
    p95_seconds: float
    input_tokens: int  # mean per question
    output_tokens: int
    cost_per_question: float | None  # dollars at list price; None = no price for this model
    cost_per_correct: float | None
    price_note: str


class ModelNotRun(BaseModel):
    llm: str
    missing: list[str]


class ModelStudy(BaseModel):
    name: str
    title: str
    suites: list[str]  # the suites these rows cover
    partial_note: str | None  # "so far only golden, business": not every suite is covered
    questions: int
    only_one_model: bool
    rows: list[ModelRow]  # most accurate first
    not_run: list[ModelNotRun]
    reports: list[str]


class ModelComparison(BaseModel):
    prices_checked: str
    notes: list[str]
    studies: list[ModelStudy]


class QualityMetric(BaseModel):
    metric: str
    asks: str
    mean: float | None
    passed: int
    judged: int
    not_scored: int


class QualityScore(BaseModel):
    metric: str
    score: float | None
    passed: bool | None


class QualityQuestion(BaseModel):
    """Only the scores: the judge's reasons and the answer quote data rows, so they stay out."""

    question_id: str
    sql_match: bool | None
    scores: list[QualityScore]
    note: str  # a hand-written warning about this score, or ""


class RetrievalQuality(BaseModel):
    questions: int
    complete: int  # questions that got every table they need
    id_recall: float
    id_precision: float
    sample: int
    llm_precision: float | None
    id_precision_sample: float | None


class QualityPanel(BaseModel):
    """Answer quality comes from its own run, not from any run in `runs`."""

    report: str
    date: str
    code: str
    agent_llm: str
    judge_llm: str
    answers: int
    judged_answers: int
    metrics: list[QualityMetric]
    questions: list[QualityQuestion]
    retrieval: RetrievalQuality | None


class GateInfo(BaseModel):
    """The CI gate's baseline. Its live status is on GitHub, at `actions_url`."""

    llm: str
    questions: int
    passed: int
    failed: int
    allowed_regressions: int
    failure_proven: bool  # a deliberately broken prompt has made it fail
    note: str
    actions_url: str


class Milestone(BaseModel):
    """A headline number. Different question sets and models: these points aren't a trend."""

    phase: str
    title: str
    question_set: str
    passed: int
    total: int
    not_scored: int = 0
    llm: str
    date: str
    report: str
    note: str = ""


class SpeedUp(BaseModel):
    title: str
    before: str
    after: str
    factor: str
    note: str
    report: str


class BirdInfo(BaseModel):
    """Who BIRD's data belongs to, and what to know about the version."""

    url: str
    licence: str
    version_note: str


class EvalsReply(BaseModel):
    format: int = SNAPSHOT_FORMAT
    bird_text_available: bool = False
    bird: BirdInfo
    questions: list[EvalQuestion]
    runs: list[EvalRun]
    ablations: list[AblationStudy]
    models: ModelComparison
    quality: QualityPanel | None
    gate: GateInfo
    milestones: list[Milestone]
    speed_ups: list[SpeedUp]


class _BirdQuestion(BaseModel):
    question_id: int
    question: str
    evidence: str = ""
    sql: str = Field(alias="SQL")

    def as_asked(self) -> str:
        """What the agent was given: BIRD's hint follows the question (evals/bird.py)."""
        hint = self.evidence.strip()
        return f"{self.question}\nHint: {hint}" if hint else self.question


_BIRD_FILE = TypeAdapter(list[_BirdQuestion])


def read_snapshot(path: Path) -> EvalsReply:
    """The file, checked. Raises EvalsUnavailableError if it's missing or doesn't fit."""
    try:
        reply = EvalsReply.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        logger.warning("evals snapshot unavailable", extra={"detail": str(exc)[:200]})
        raise EvalsUnavailableError("The eval results aren't available on this server.") from exc
    if reply.format != SNAPSHOT_FORMAT:
        raise EvalsUnavailableError("The eval results were saved in another format.")
    return reply


def _read_bird_text(path: Path) -> dict[str, _BirdQuestion]:
    """Our id → the question. Empty if the file isn't there or doesn't fit: never an error."""
    try:
        questions = _BIRD_FILE.validate_json(path.read_bytes())
    except FileNotFoundError:
        return {}
    except (OSError, ValidationError) as exc:
        logger.warning("bird questions unreadable", extra={"detail": str(exc)[:200]})
        return {}
    return {f"bird_{q.question_id}": q for q in questions}


def with_bird_text(reply: EvalsReply, path: Path) -> EvalsReply:
    """The reply with BIRD's question text filled in, if the downloaded file has it."""
    text = _read_bird_text(path)
    bird_ids = {q.id for q in reply.questions if q.study == "bird"}
    if not bird_ids or not bird_ids <= text.keys():
        return reply
    questions = [
        q.model_copy(update={"question": text[q.id].as_asked(), "reference_sql": text[q.id].sql})
        if q.study == "bird"
        else q
        for q in reply.questions
    ]
    return reply.model_copy(update={"questions": questions, "bird_text_available": True})


class EvalsData:
    """The reply, built once and reused. A failed read isn't kept, so a new file is found."""

    def __init__(self, snapshot_path: Path, bird_questions_path: Path) -> None:
        self._snapshot_path = snapshot_path
        self._bird_questions_path = bird_questions_path
        self._reply: EvalsReply | None = None
        self._lock = threading.Lock()

    def reply(self) -> EvalsReply:
        with self._lock:
            if self._reply is None:
                self._reply = with_bird_text(
                    read_snapshot(self._snapshot_path), self._bird_questions_path
                )
            return self._reply


def create_evals(settings: Settings) -> EvalsData:
    return EvalsData(settings.evals_snapshot_path, settings.bird_questions_path)


def get_evals(request: Request) -> EvalsData:
    """The evals data created at startup (see main.py), stored on the app."""
    evals: EvalsData = request.app.state.evals
    return evals


@router.get(
    "/evals",
    response_model=EvalsReply,
    responses={503: {"model": ErrorResponse, "description": "The results file isn't available"}},
)
def get_eval_results(evals: Annotated[EvalsData, Depends(get_evals)]) -> EvalsReply:
    """Every saved eval result: scores, ablations, models, answer quality and the CI baseline."""
    return evals.reply()
