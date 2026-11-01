"""What the agent is doing right now, as a stream of small events (UI U2.1).

    for event in pipeline.stream("Top 5 products?"):
        ...  # StepEvent(node="generate_sql", status="started"), SqlEvent(attempt=1, ...), ...

The web UI draws these live. `TraceBuilder` turns LangGraph's raw stream into them, so the rest
of the code never sees LangGraph's chunks.

An event may hold only what a person checking the answer may see (§8.5). Never prompts or the
schema text sent to the LLM, never rows (they come with the final reply), and never a `detail`
or a fallback `reason`: those are written for the LLM and the logs, and can hold raw vendor text.
So each event names its fields one by one instead of carrying the agent's own models.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict

from querymind.agent.schemas import AgentReply, ShownSchema, SqlAttempt
from querymind.semantic.retrieval import SchemaSelection

StepName = Literal[
    "retrieve_schema", "generate_sql", "run_sql", "check_result", "repair_sql", "summarize"
]
_STEP_NAMES: tuple[str, ...] = get_args(StepName)  # `remember` is bookkeeping: not reported

# What `_run_sql` sends through LangGraph's stream writer when a query passes the validator.
VALIDATED_SIGNAL = "validated"


class _Event(BaseModel):
    model_config = ConfigDict(frozen=True)


class StepEvent(_Event):
    """A node of the graph started, finished or failed. `duration_ms` is set when it ended."""

    type: Literal["step"] = "step"
    node: StepName
    status: Literal["started", "finished", "failed"]
    duration_ms: int | None = None


class SchemaEvent(_Event):
    """Which tables the LLM was shown."""

    type: Literal["schema"] = "schema"
    schema_selection: ShownSchema


class RewriteEvent(_Event):
    """A follow-up was searched as this standalone question."""

    type: Literal["rewrite"] = "rewrite"
    search_question: str


class ExamplesEvent(_Event):
    """The questions of the solved examples shown to the LLM."""

    type: Literal["examples"] = "examples"
    questions: list[str]


class SqlEvent(_Event):
    """Try number `attempt`: the LLM's SQL, not validated yet."""

    type: Literal["sql"] = "sql"
    attempt: int
    sql: str
    explanation: str


class ValidatedEvent(_Event):
    """Try number `attempt` passed the SQL validator and is about to run."""

    type: Literal["validated"] = "validated"
    attempt: int


class AttemptFailedEvent(_Event):
    """Try number `attempt` failed, or ran but looked wrong ("suspicious")."""

    type: Literal["attempt_failed"] = "attempt_failed"
    attempt: int
    kind: Literal["unsafe", "db_error", "timeout", "suspicious"]
    message: str


class EventWarning(_Event):
    """A sign the result may be wrong: the words for people, not the hint for the LLM."""

    code: str
    message: str


class CheckedEvent(_Event):
    """The result was checked: these are the warning signs (none = it looks fine)."""

    type: Literal["checked"] = "checked"
    warnings: list[EventWarning]


type AgentEvent = (
    StepEvent
    | SchemaEvent
    | RewriteEvent
    | ExamplesEvent
    | SqlEvent
    | ValidatedEvent
    | AttemptFailedEvent
    | CheckedEvent
)


@dataclass(frozen=True)
class Finished:
    """The last item of `Pipeline.stream`: the reply. Not an event: the API wraps it."""

    reply: AgentReply


def shown_schema(selection: SchemaSelection, *, total_tables: int) -> ShownSchema:
    """The safe part of a SchemaSelection. `reason` stays behind: it can hold raw exception text."""
    return ShownSchema(
        mode=selection.mode,
        tables=list(selection.tables),
        total_tables=total_tables,
        search=selection.search,
    )


class TraceBuilder:
    """Turns one run's LangGraph chunks into events. Keeps the try count and each node's start."""

    def __init__(self, *, total_tables: int) -> None:
        self._total_tables = total_tables
        self._attempt = 0  # the SQL try we are on; a draft that asks back or refuses isn't one
        self._started: dict[str, float] = {}

    def events(self, mode: str, chunk: Any) -> list[AgentEvent]:
        """The events for one chunk of `stream_mode=["tasks", "custom"]`; often none."""
        if mode == "custom":
            return [ValidatedEvent(attempt=self._attempt)] if chunk == VALIDATED_SIGNAL else []
        node = chunk["name"]
        if node not in _STEP_NAMES:
            return []
        # A task sends two chunks with the same id: `input` when it starts, `result` when it ends.
        if "input" in chunk:
            self._started[chunk["id"]] = time.perf_counter()
            return [StepEvent(node=node, status="started")]
        milliseconds = round((time.perf_counter() - self._started.pop(chunk["id"])) * 1000)
        status: Literal["finished", "failed"] = "failed" if chunk["error"] else "finished"
        return [
            *self._details(node, chunk["result"]),
            StepEvent(node=node, status=status, duration_ms=milliseconds),
        ]

    def _details(self, node: str, writes: Mapping[str, Any]) -> list[AgentEvent]:
        """What a finished node found out, from the fields it wrote to the state."""
        if node == "retrieve_schema":
            return self._retrieved(writes)
        if node in ("generate_sql", "repair_sql"):
            draft = writes.get("draft")
            if draft is None or not draft.wants_sql:
                return []
            self._attempt += 1
            return [SqlEvent(attempt=self._attempt, sql=draft.sql, explanation=draft.explanation)]
        if node == "check_result":
            warnings = [
                EventWarning(code=w.code, message=w.message) for w in writes.get("warnings", [])
            ]
            return [CheckedEvent(warnings=warnings), *self._failed(writes)]
        return self._failed(writes)

    def _retrieved(self, writes: Mapping[str, Any]) -> list[AgentEvent]:
        events: list[AgentEvent] = []
        if (selection := writes.get("schema_selection")) is not None:
            shown = shown_schema(selection, total_tables=self._total_tables)
            events.append(SchemaEvent(schema_selection=shown))
        if search_question := writes.get("search_question"):
            events.append(RewriteEvent(search_question=search_question))
        if examples := writes.get("examples"):
            events.append(ExamplesEvent(questions=[e.question for e in examples]))
        return events

    def _failed(self, writes: Mapping[str, Any]) -> list[AgentEvent]:
        attempts: list[SqlAttempt] = writes.get("attempts", [])
        return [
            AttemptFailedEvent(attempt=self._attempt, kind=a.error_kind, message=a.message)
            for a in attempts
        ]
