"""Does QueryMind ask back when it should, and only then? (Phase 2.4)

    uv run python -m evals.clarify                    # all 12 cases
    uv run python -m evals.clarify --ids c01_best_customers a03_customers_most_orders

Each case says which KIND of reply is right: a clarifying question ("clarify"), an answer
("answer") or a refusal ("refuse"). The suite checks only the kind, not what was said or
computed: the golden set (evals/run.py) already measures whether answers are right.
Uses real LLM calls: not run in CI.
"""

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from evals.golden_set import GOLDEN_DIR, GoldenSetError
from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest
from querymind.config import ConfigError, get_settings
from querymind.db.errors import QueryError
from querymind.llm import LLMError, LLMProviderError, create_llm_provider
from querymind.safety.errors import UnsafeSqlError

CLARIFY_FILE = GOLDEN_DIR / "clarification.yaml"

Kind = Literal["clarify", "answer", "refuse"]
KINDS: tuple[Kind, ...] = ("clarify", "answer", "refuse")


class ClarifyCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    question: str = Field(min_length=1)
    expect: Kind
    why: str = Field(min_length=1)  # the readings (or the reason): makes each case checkable


class ClarifySet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str
    version: int
    cases: tuple[ClarifyCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        ids = [case.id for case in self.cases]
        if repeated := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate case ids: {', '.join(repeated)}")
        return self


def load_clarify_set(path: Path = CLARIFY_FILE) -> ClarifySet:
    """Read and check the YAML. Raises GoldenSetError with a readable message."""
    try:
        return ClarifySet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise GoldenSetError(f"{path.name}: {exc}") from exc


@dataclass(frozen=True)
class CaseOutcome:
    case: ClarifyCase
    got: Kind | None  # None = no verdict (the LLM provider was unavailable)
    detail: str  # what it asked or said, or the error

    @property
    def passed(self) -> bool:
        return self.got == self.case.expect


def evaluate_case(case: ClarifyCase, ask: Callable[[str], AgentReply]) -> CaseOutcome:
    """Ask one question and record which kind of reply came back."""
    try:
        reply = ask(case.question)
    except (UnsafeSqlError, QueryError, ValueError) as exc:
        # It tried to answer (wrote SQL) and the SQL failed: still an "answer", not a question.
        return CaseOutcome(case, "answer", f"{type(exc).__name__}: {exc}")
    except LLMProviderError as exc:
        return CaseOutcome(case, None, f"LLMProviderError: {exc}")
    except LLMError as exc:
        return CaseOutcome(case, "answer", f"{type(exc).__name__}: {exc}")
    if isinstance(reply, ClarificationRequest):
        options = " / ".join(reply.clarification.options)
        return CaseOutcome(case, "clarify", f"{reply.clarification.question} [{options}]")
    if isinstance(reply, AskResult):
        return CaseOutcome(case, "answer", reply.sql)
    return CaseOutcome(case, "refuse", reply.message)


def score_line(outcomes: Sequence[CaseOutcome], expect: Kind) -> str:
    group = [o for o in outcomes if o.case.expect == expect and o.got is not None]
    labels = {
        "clarify": "asked back on ambiguous questions",
        "answer": "answered clear questions (no false alarm)",
        "refuse": "refused data changes",
    }
    return f"{labels[expect]}: {sum(o.passed for o in group)}/{len(group)}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Does QueryMind ask back only when it should?")
    parser.add_argument("--ids", nargs="+", help="only these case ids")
    parser.add_argument("--delay", type=float, default=1.0, help="seconds between questions")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    cases = [c for c in load_clarify_set().cases if not args.ids or c.id in args.ids]
    try:
        settings = get_settings()
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    pipeline = Pipeline(llm=create_llm_provider(settings), settings=settings)

    def ask(question: str) -> AgentReply:
        return pipeline.ask(question, summarize_answer=False)

    print(f"Clarification suite: {len(cases)} cases\n")
    outcomes = []
    for number, case in enumerate(cases, start=1):
        if number > 1:
            time.sleep(args.delay)
        outcome = evaluate_case(case, ask)
        outcomes.append(outcome)
        status = "SKIP" if outcome.got is None else "PASS" if outcome.passed else "FAIL"
        print(f"[{number:2}/{len(cases)}] {status:4} {case.id:32} expect {case.expect:7} "
              f"got {outcome.got or '-':7} {outcome.detail[:80]}")  # fmt: skip

    print()
    for expect in KINDS:
        print(score_line(outcomes, expect))
    return 0


if __name__ == "__main__":
    sys.exit(main())
