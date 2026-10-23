"""The agent as a LangGraph graph: nodes (steps) joined by edges (what runs next).

    graph = build_graph(checkpointer=InMemorySaver())
    final = graph.invoke(
        new_turn(question=..., schema_text=..., table_names=[...]),
        {"configurable": {"thread_id": conversation_id}},
        context=AgentContext(llm=llm, settings=settings, connect=connect_readonly),
    )

    START → retrieve_schema → generate_sql ─┬─ refuses / asks back ─► remember (no SQL runs)
                                            └─► run_sql ─┬─ ran ──────────────────► check_result
                                           ▲     ├─ failed, tries left ───► repair_sql
                                           │     ├─ failed, result kept ──► summarize
                                           │     └─ failed, no result ────► remember
                                           │   check_result ─┬─ looks fine ──► summarize → remember
                                           │                 └─ looks wrong ─► repair_sql
                                           └── repair_sql (every repaired query is validated again)

Schema retrieval (3.3): `retrieve_schema` narrows `schema_text` to the tables this question
needs (semantic/retrieval.py), when the run's context has a retriever. Without one (a small
schema, or SCHEMA_RETRIEVAL=off) it only records that the whole schema was sent. The table
allowlist is never narrowed, and a repair sees a wider text than the first try.

Self-correction (2.2): when the SQL is unsafe, the database rejects it, or it times out,
`run_sql` records the failed attempt instead of raising, and the graph sends the error back
to the LLM (`repair_sql`), up to `settings.max_sql_attempts` queries in total.

Sanity check (2.3): a query that runs can still be wrong. `check_result` looks for warning
signs (agent/sanity.py). An empty result or duplicate rows get one repair; if it doesn't
produce a better result, the first result is answered with its warnings shown.

Clarify or refuse (2.4): the LLM's draft can instead hold a clarifying question (several
reasonable meanings, e.g. "best customers") or a refusal (a request to change data). The run
then ends before any SQL, and `Pipeline.ask` returns a ClarificationRequest or a Refusal.

If every try fails and no result exists, the run ends and `Pipeline.ask` raises the last
error. LLM errors and an unreachable database end the run at once (the caller retries those).

Conversations (2.6): every way of ending passes through `remember`, which adds this turn to
`history` (question, SQL, result shape; no rows). With a checkpointer, LangGraph saves the
state per `thread_id`, so the next turn of that conversation starts from it: the LLM sees the
history and can build on the earlier SQL ("now only for 1997").

Print the current diagram with `uv run python scripts/show_graph.py`.
"""

import logging

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from querymind.agent.sanity import RETRY_CODES, check_result
from querymind.agent.schemas import AnswerSummary, SqlAttempt, Turn
from querymind.agent.sql_generator import generate_sql, repair_sql
from querymind.agent.state import AgentContext, AgentState, require
from querymind.agent.summarizer import fallback_answer, summarize
from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.db.executor import execute_query
from querymind.db.schema import estimate_tokens
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.retrieval import SchemaSelection

logger = logging.getLogger(__name__)

type AgentGraph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]

# The failures the LLM can fix by writing different SQL. Nothing else is retried.
FIXABLE_ERRORS = (UnsafeSqlError, QueryExecutionError, QueryTimeoutError)


def build_graph(*, checkpointer: BaseCheckpointSaver[str] | None = None) -> AgentGraph:
    """Wire the nodes together. Build it once and reuse it: a compiled graph is thread-safe.

    With a `checkpointer`, every run must name its conversation (`thread_id`), and its final
    state is saved for the next turn. Without one, each run starts from nothing.
    """
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("retrieve_schema", _retrieve_schema)
    graph.add_node("generate_sql", _generate_sql)
    graph.add_node("run_sql", _run_sql)
    graph.add_node("check_result", _check_result)
    graph.add_node("repair_sql", _repair_sql)
    graph.add_node("summarize", _summarize)
    graph.add_node("remember", _remember)
    graph.add_edge(START, "retrieve_schema")
    graph.add_edge("retrieve_schema", "generate_sql")
    # A draft can be a refusal or a clarifying question instead of SQL (2.4): then stop.
    graph.add_conditional_edges("generate_sql", _after_generate, ["run_sql", "remember"])
    # Conditional edges: the function looks at the state and names the next node.
    graph.add_conditional_edges(
        "run_sql", _after_run, ["check_result", "repair_sql", "summarize", "remember"]
    )
    graph.add_conditional_edges("check_result", _after_check, ["repair_sql", "summarize"])
    # The loop: a repaired query goes back to run_sql, so it is validated again.
    graph.add_conditional_edges("repair_sql", _after_repair, ["run_sql", "summarize", "remember"])
    graph.add_edge("summarize", "remember")
    # Every turn, however it ended, is written to the history before the run stops (2.6).
    graph.add_edge("remember", END)
    return graph.compile(checkpointer=checkpointer)


def _retrieve_schema(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Question (+ the conversation so far) → only the relevant tables in `schema_text`."""
    retriever = runtime.context.retriever
    if retriever is None:
        full = SchemaSelection(
            mode="full",
            tables=tuple(state["table_names"]),
            schema_tokens=estimate_tokens(state["schema_text"]),
        )
        return {"schema_selection": full}
    selection = retriever.select(state["question"], history=state.get("history", []))
    return {
        "schema_text": selection.schema_text,
        "repair_schema_text": selection.repair_schema_text,
        "schema_selection": selection.info,
        "examples": list(selection.examples),  # few-shot (3.5), for the first try and repairs
    }


def _generate_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Question + schema + the conversation so far → the LLM's SQL draft (not trusted yet)."""
    draft = generate_sql(
        runtime.context.llm,
        schema_text=state["schema_text"],
        question=state["question"],
        history=state.get("history", []),
        examples=state.get("examples", []),
    )
    return {"draft": draft}


def _after_generate(state: AgentState) -> str:
    """SQL to run, or stop: Pipeline.ask returns the refusal or the clarifying question."""
    return "run_sql" if require(state.get("draft"), "draft").wants_sql else "remember"


def _run_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Validate the draft, then run it read-only. Both in one node, on purpose.

    The validated query never leaves this function, so nothing can run SQL that skipped the
    validator (§8.2). Unsafe SQL is rejected before a connection is even opened.

    A fixable failure is returned as a new attempt (with the SQL that was really tried: the
    validated one if it got that far) instead of raised, so the graph can route to a repair.
    A success replaces `sql`, `explanation` and `result` together.
    """
    settings = runtime.context.settings
    draft = require(state.get("draft"), "draft")
    sql = draft.sql
    try:
        query = validate_sql(sql, allowed_tables=state["table_names"], max_rows=settings.max_rows)
        sql = query.sql
        with runtime.context.connect(settings) as conn:
            result = execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds)
    except FIXABLE_ERRORS as exc:
        attempt = _failed_attempt(sql, exc)
        logger.info(
            "sql attempt failed",
            extra={
                "attempt": len(state.get("attempts", [])) + 1,
                "error_kind": attempt.error_kind,
                "detail": attempt.detail,
            },
        )
        return {"attempts": [attempt], "needs_repair": True}
    return {
        "sql": sql,
        "explanation": draft.explanation,
        "result": result,
        "self_assessment": draft.self_assessment,
        "needs_repair": False,
    }


def _after_run(state: AgentState, runtime: Runtime[AgentContext]) -> str:
    """Where to go after run_sql: check the result, try again, or stop."""
    if not state["needs_repair"]:
        return "check_result"
    if _tries_left(state, runtime):
        return "repair_sql"
    if state.get("result") is not None:  # the repair of a suspicious result failed: answer it
        return "summarize"
    return "remember"  # nothing ran: Pipeline.ask raises the last attempt's error


def _check_result(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Look for signs the result is wrong. Empty or duplicated rows get one repair."""
    warnings = check_result(require(state.get("result"), "result"))
    retryable = [w for w in warnings if w.code in RETRY_CODES]
    already_retried = any(a.error_kind == "suspicious" for a in state.get("attempts", []))
    # The query that just ran isn't an attempt yet: it would become one, so count it.
    if retryable and not already_retried and _tries_left(state, runtime, pending=1):
        suspicious = SqlAttempt(
            sql=require(state.get("sql"), "sql"),
            error_kind="suspicious",
            message=retryable[0].message,
            detail=" ".join(w.detail for w in retryable),
        )
        logger.info("result looks wrong", extra={"codes": [w.code for w in retryable]})
        return {"warnings": warnings, "attempts": [suspicious], "needs_repair": True}
    return {"warnings": warnings}


def _after_check(state: AgentState) -> str:
    return "repair_sql" if state["needs_repair"] else "summarize"


def _repair_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Every failed or suspicious attempt + why → a new draft from the LLM.

    With retrieval, the repair sees the chosen tables plus their neighbours: if the first try
    failed because a table was left out, it's now in view.
    """
    draft = repair_sql(
        runtime.context.llm,
        schema_text=state.get("repair_schema_text") or state["schema_text"],
        question=state["question"],
        failed=state["attempts"],
        history=state.get("history", []),
        examples=state.get("examples", []),
    )
    return {"draft": draft}


def _after_repair(state: AgentState) -> str:
    """Run the repaired query; or, if the LLM now asks or refuses instead, stop there.

    With an earlier (suspicious) result in hand, answer it rather than dropping it.
    """
    if require(state.get("draft"), "draft").wants_sql:
        return "run_sql"
    return "summarize" if state.get("result") is not None else "remember"


def _summarize(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Rows → a plain-language answer, checked against the data (or the templated answer)."""
    result = require(state.get("result"), "result")
    if runtime.context.summarize_answer:
        answer = summarize(runtime.context.llm, question=state["question"], result=result)
    else:
        answer = AnswerSummary(text=fallback_answer(result), from_llm=False)
    return {"answer": answer}


def _remember(state: AgentState) -> AgentState:
    """Add this turn to the conversation history (2.6); the reducer keeps the last few."""
    return {"history": [turn_from(state)]}


def turn_from(state: AgentState) -> Turn:
    """What the next turns need to know about this one, in the order Pipeline.ask replies.

    An answer keeps its SQL and the result's shape, never its rows (§8.5).
    """
    question = state["question"]
    result = state.get("result")
    if result is not None:
        return Turn(
            question=question,
            kind="answer",
            sql=state.get("sql"),
            columns=result.columns,
            row_count=result.row_count,
        )
    draft = state.get("draft")
    if draft is not None and draft.refusal is not None:
        return Turn(question=question, kind="refusal", reply=draft.refusal)
    if draft is not None and draft.clarification is not None:
        asked = draft.clarification
        reply = f"{asked.question} Options: {'; '.join(asked.options)}"
        return Turn(question=question, kind="clarification", reply=reply)
    return Turn(question=question, kind="failed")


def _tries_left(state: AgentState, runtime: Runtime[AgentContext], *, pending: int = 0) -> bool:
    """Is there room for one more query? Each recorded attempt was one query, plus `pending`
    queries that ran but aren't recorded yet."""
    used = len(state.get("attempts", [])) + pending
    return used < runtime.context.settings.max_sql_attempts


def _failed_attempt(sql: str, exc: Exception) -> SqlAttempt:
    """Record a fixable failure: a message for people, a detail for the LLM."""
    if isinstance(exc, UnsafeSqlError):
        return SqlAttempt(sql=sql, error_kind="unsafe", message=exc.reason, detail=exc.reason)
    if isinstance(exc, QueryExecutionError):
        return SqlAttempt(sql=sql, error_kind="db_error", message=str(exc), detail=exc.detail)
    return SqlAttempt(sql=sql, error_kind="timeout", message=str(exc), detail=str(exc))
