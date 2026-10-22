"""What the agent graph carries while it answers one question.

Two kinds of things travel through a run, and they are kept apart on purpose:

- `AgentState`: the *data* of this question (question, SQL, result, answer). Every node reads
  it and returns the fields it changed; LangGraph merges them in. It holds only plain values
  and Pydantic models, so it can be saved and reloaded: the checkpointer saves it after every
  turn of a conversation (2.6).
- `AgentContext`: the *tools* for this run (LLM, settings, how to connect). Passed with
  `graph.invoke(..., context=...)`, never saved, never part of the state.

Never put a `ValidatedQuery` or a DB connection in the state: a connection can't be saved, and
a reloaded `ValidatedQuery` would skip the validator's guarantee (§8.2). The state keeps the
SQL as text; `run_sql` validates it right before running it.

Conversations (2.6): the saved state of turn 1 is the starting state of turn 2. So every field
except `history` belongs to one turn and is reset by `new_turn` (a saved key can't be removed,
only set to None). Check "is there a result?" with `state.get("result") is not None`, never with
`"result" in state`: after turn 1 the key is always there.
"""

import operator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, TypedDict

import psycopg
from langgraph.types import Overwrite

from querymind.agent.schemas import (
    AnswerSummary,
    Clarification,
    ConfidenceLevel,
    SanityWarning,
    SqlAttempt,
    SqlDraft,
    Turn,
)
from querymind.config import Settings
from querymind.db.executor import QueryResult
from querymind.llm import LLMProvider

MAX_HISTORY_TURNS = 5  # earlier turns the LLM sees; older ones are forgotten


def keep_last_turns(saved: list[Turn], new: list[Turn]) -> list[Turn]:
    """The reducer of `history`: append the new turn, keep only the last few."""
    return (saved + new)[-MAX_HISTORY_TURNS:]


class AgentState(TypedDict, total=False):
    """The shared notebook of one run. `total=False`: each node fills in only its own fields."""

    # Set by Pipeline.ask before the run starts.
    question: str
    schema_text: str  # the schema description the LLM sees
    table_names: list[str]  # the validator's table allowlist
    # Filled in by the nodes.
    draft: SqlDraft | None  # the query to try next (from generate_sql or repair_sql), not trusted
    # The last query that ran, as a set: its validated SQL, the LLM's explanation, its rows.
    # A later failed repair doesn't touch them, so a good-enough result is never lost.
    sql: str | None
    explanation: str | None
    result: QueryResult | None
    self_assessment: ConfidenceLevel | None  # the LLM's own view of the query that ran
    warnings: list[SanityWarning]  # signs that `result` may be wrong (check_result)
    # Failed or suspicious tries, oldest first. `operator.add` is a *reducer*: a node returns
    # only its new attempt ({"attempts": [one]}) and LangGraph appends it, instead of replacing.
    attempts: Annotated[list[SqlAttempt], operator.add]
    needs_repair: bool  # True after a failed or suspicious query: route to repair_sql
    answer: AnswerSummary | None
    # The conversation so far (2.6): the only field that outlives a turn.
    history: Annotated[list[Turn], keep_last_turns]


def new_turn(*, question: str, schema_text: str, table_names: list[str]) -> AgentState:
    """The input of one turn: this question, and every per-turn field reset.

    Without the reset, turn 2 would start with turn 1's result, attempts and answer, e.g. a
    failed query would be "answered" with the previous question's rows.
    """
    return {
        "question": question,
        "schema_text": schema_text,
        "table_names": table_names,
        "draft": None,
        "sql": None,
        "explanation": None,
        "result": None,
        "self_assessment": None,
        "warnings": [],
        # A reducer would *append* [] to the saved attempts (old + [] = old). Overwrite tells
        # LangGraph to replace the value instead, once.
        "attempts": Overwrite([]),  # type: ignore[typeddict-item]
        "needs_repair": False,
        "answer": None,
    }


# The classes the checkpointer may rebuild when it loads a saved state. LangGraph warns about
# (and will soon block) any other class: a saved checkpoint shouldn't create arbitrary objects.
CHECKPOINT_TYPES = [
    (cls.__module__, cls.__name__)
    for cls in (
        QueryResult,
        AnswerSummary,
        Clarification,
        SanityWarning,
        SqlAttempt,
        SqlDraft,
        Turn,
    )
]


def require[T](value: T | None, field: str) -> T:
    """A per-turn field an earlier node always fills in. None means the graph is wired wrong."""
    if value is None:
        raise RuntimeError(f"agent state has no {field} at this point")
    return value


@dataclass(frozen=True)
class AgentContext:
    """The tools one run uses. Not saved with the state."""

    llm: LLMProvider
    settings: Settings
    connect: Callable[[Settings], psycopg.Connection]  # must open a *read-only* connection
    summarize_answer: bool = True  # False: templated answer, no second LLM call (evals)
