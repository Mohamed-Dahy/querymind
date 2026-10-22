"""What the agent graph carries while it answers one question.

Two kinds of things travel through a run, and they are kept apart on purpose:

- `AgentState`: the *data* of this question (question, SQL, result, answer). Every node reads
  it and returns the fields it changed; LangGraph merges them in. It holds only plain values
  and Pydantic models, so it can be saved and reloaded (Phase 2.6 saves it per conversation).
- `AgentContext`: the *tools* for this run (LLM, settings, how to connect). Passed with
  `graph.invoke(..., context=...)`, never saved, never part of the state.

Never put a `ValidatedQuery` or a DB connection in the state: a connection can't be saved, and
a reloaded `ValidatedQuery` would skip the validator's guarantee (§8.2). The state keeps the
SQL as text; `run_sql` validates it right before running it.
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, TypedDict

import psycopg

from querymind.agent.schemas import (
    AnswerSummary,
    ConfidenceLevel,
    SanityWarning,
    SqlAttempt,
    SqlDraft,
)
from querymind.config import Settings
from querymind.db.executor import QueryResult
from querymind.llm import LLMProvider


class AgentState(TypedDict, total=False):
    """The shared notebook of one run. `total=False`: each node fills in only its own fields."""

    # Set by Pipeline.ask before the run starts.
    question: str
    schema_text: str  # the schema description the LLM sees
    table_names: list[str]  # the validator's table allowlist
    # Filled in by the nodes.
    draft: SqlDraft  # the query to try next (from generate_sql or repair_sql), not trusted yet
    # The last query that ran, as a set: its validated SQL, the LLM's explanation, its rows.
    # A later failed repair doesn't touch them, so a good-enough result is never lost.
    sql: str
    explanation: str
    result: QueryResult
    self_assessment: ConfidenceLevel | None  # the LLM's own view of the query that ran
    warnings: list[SanityWarning]  # signs that `result` may be wrong (check_result)
    # Failed or suspicious tries, oldest first. `operator.add` is a *reducer*: a node returns
    # only its new attempt ({"attempts": [one]}) and LangGraph appends it, instead of replacing.
    attempts: Annotated[list[SqlAttempt], operator.add]
    needs_repair: bool  # True after a failed or suspicious query: route to repair_sql
    answer: AnswerSummary


@dataclass(frozen=True)
class AgentContext:
    """The tools one run uses. Not saved with the state."""

    llm: LLMProvider
    settings: Settings
    connect: Callable[[Settings], psycopg.Connection]  # must open a *read-only* connection
    summarize_answer: bool = True  # False: templated answer, no second LLM call (evals)
