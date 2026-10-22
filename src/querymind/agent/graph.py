"""The agent as a LangGraph graph: nodes (steps) joined by edges (what runs next).

    graph = build_graph()
    final = graph.invoke(
        {"question": ..., "schema_text": ..., "table_names": [...]},
        context=AgentContext(llm=llm, settings=settings, connect=connect_readonly),
    )

    START → generate_sql → run_sql ─┬─ ran ──────────────────► check_result
                              ▲     ├─ failed, tries left ───► repair_sql
                              │     ├─ failed, result kept ──► summarize
                              │     └─ failed, no result ────► END
                              │   check_result ─┬─ looks fine ──► summarize → END
                              │                 └─ looks wrong ─► repair_sql
                              └── repair_sql (every repaired query is validated again)

Self-correction (2.2): when the SQL is unsafe, the database rejects it, or it times out,
`run_sql` records the failed attempt instead of raising, and the graph sends the error back
to the LLM (`repair_sql`), up to `settings.max_sql_attempts` queries in total.

Sanity check (2.3): a query that runs can still be wrong. `check_result` looks for warning
signs (agent/sanity.py). An empty result or duplicate rows get one repair; if it doesn't
produce a better result, the first result is answered with its warnings shown.

If every try fails and no result exists, the run ends and `Pipeline.ask` raises the last
error. LLM errors and an unreachable database end the run at once (the caller retries those).

Print the current diagram with `uv run python scripts/show_graph.py`.
"""

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from querymind.agent.sanity import RETRY_CODES, check_result
from querymind.agent.schemas import AnswerSummary, SqlAttempt
from querymind.agent.sql_generator import generate_sql, repair_sql
from querymind.agent.state import AgentContext, AgentState
from querymind.agent.summarizer import fallback_answer, summarize
from querymind.db.errors import QueryExecutionError, QueryTimeoutError
from querymind.db.executor import execute_query
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql

logger = logging.getLogger(__name__)

type AgentGraph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]

# The failures the LLM can fix by writing different SQL. Nothing else is retried.
FIXABLE_ERRORS = (UnsafeSqlError, QueryExecutionError, QueryTimeoutError)


def build_graph() -> AgentGraph:
    """Wire the nodes together. Build it once and reuse it: a compiled graph is thread-safe."""
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("generate_sql", _generate_sql)
    graph.add_node("run_sql", _run_sql)
    graph.add_node("check_result", _check_result)
    graph.add_node("repair_sql", _repair_sql)
    graph.add_node("summarize", _summarize)
    graph.add_edge(START, "generate_sql")
    graph.add_edge("generate_sql", "run_sql")
    # Conditional edges: the function looks at the state and names the next node.
    graph.add_conditional_edges(
        "run_sql", _after_run, ["check_result", "repair_sql", "summarize", END]
    )
    graph.add_conditional_edges("check_result", _after_check, ["repair_sql", "summarize"])
    graph.add_edge("repair_sql", "run_sql")  # the loop: a repaired query is validated again
    graph.add_edge("summarize", END)
    return graph.compile()


def _generate_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Question + schema → the LLM's SQL draft (not trusted yet)."""
    draft = generate_sql(
        runtime.context.llm, schema_text=state["schema_text"], question=state["question"]
    )
    return {"draft": draft}


def _run_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Validate the draft, then run it read-only. Both in one node, on purpose.

    The validated query never leaves this function, so nothing can run SQL that skipped the
    validator (§8.2). Unsafe SQL is rejected before a connection is even opened.

    A fixable failure is returned as a new attempt (with the SQL that was really tried: the
    validated one if it got that far) instead of raised, so the graph can route to a repair.
    A success replaces `sql`, `explanation` and `result` together.
    """
    settings = runtime.context.settings
    draft = state["draft"]
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
    return {"sql": sql, "explanation": draft.explanation, "result": result, "needs_repair": False}


def _after_run(state: AgentState, runtime: Runtime[AgentContext]) -> str:
    """Where to go after run_sql: check the result, try again, or stop."""
    if not state["needs_repair"]:
        return "check_result"
    if _tries_left(state, runtime):
        return "repair_sql"
    if "result" in state:  # the repair of a suspicious result failed: answer the first one
        return "summarize"
    return END  # nothing ran: Pipeline.ask raises the last attempt's error


def _check_result(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Look for signs the result is wrong. Empty or duplicated rows get one repair."""
    warnings = check_result(state["result"])
    retryable = [w for w in warnings if w.code in RETRY_CODES]
    already_retried = any(a.error_kind == "suspicious" for a in state.get("attempts", []))
    # The query that just ran isn't an attempt yet: it would become one, so count it.
    if retryable and not already_retried and _tries_left(state, runtime, pending=1):
        suspicious = SqlAttempt(
            sql=state["sql"],
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
    """Every failed or suspicious attempt + why → a new draft from the LLM."""
    draft = repair_sql(
        runtime.context.llm,
        schema_text=state["schema_text"],
        question=state["question"],
        failed=state["attempts"],
    )
    return {"draft": draft}


def _summarize(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Rows → a plain-language answer, checked against the data (or the templated answer)."""
    result = state["result"]
    if runtime.context.summarize_answer:
        answer = summarize(runtime.context.llm, question=state["question"], result=result)
    else:
        answer = AnswerSummary(text=fallback_answer(result), from_llm=False)
    return {"answer": answer}


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
