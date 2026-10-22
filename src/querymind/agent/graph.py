"""The agent as a LangGraph graph: nodes (steps) joined by edges (what runs next).

    graph = build_graph()
    final = graph.invoke(
        {"question": ..., "schema_text": ..., "table_names": [...]},
        context=AgentContext(llm=llm, settings=settings, connect=connect_readonly),
    )

    START → generate_sql → run_sql ─┬─ worked ──────────────► summarize → END
                              ▲     ├─ failed, tries left ──► repair_sql ─┐
                              │     └─ failed, no tries left ─► END       │
                              └───────────────────────────────────────────┘

Self-correction (2.2): when the SQL is unsafe, the database rejects it, or it times out,
`run_sql` records the failed attempt instead of raising, and the graph sends the error back
to the LLM (`repair_sql`), up to `settings.max_sql_attempts` queries in total. If every try
fails, the run ends without a result and `Pipeline.ask` raises the last error.

Other errors are not retried here: LLM errors and an unreachable database end the run at once
(retrying those later is the caller's job, e.g. the eval's backoff).

Print the current diagram with `uv run python scripts/show_graph.py`.
"""

import logging

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

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
    graph.add_node("repair_sql", _repair_sql)
    graph.add_node("summarize", _summarize)
    graph.add_edge(START, "generate_sql")
    graph.add_edge("generate_sql", "run_sql")
    # A conditional edge: `_after_run` looks at the state and names the next node.
    graph.add_conditional_edges("run_sql", _after_run, ["summarize", "repair_sql", END])
    graph.add_edge("repair_sql", "run_sql")  # the loop: a repaired query is validated again
    graph.add_edge("summarize", END)
    return graph.compile()


def _generate_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Question + schema → the LLM's SQL draft (not trusted yet)."""
    draft = generate_sql(
        runtime.context.llm, schema_text=state["schema_text"], question=state["question"]
    )
    return {"sql": draft.sql, "explanation": draft.explanation}


def _run_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Validate the draft, then run it read-only. Both in one node, on purpose.

    The validated query never leaves this function, so nothing can run SQL that skipped the
    validator (§8.2). Unsafe SQL is rejected before a connection is even opened.

    A fixable failure is returned as a new attempt (with the SQL that was really tried: the
    validated one if it got that far) instead of raised, so the graph can route to a repair.
    """
    settings = runtime.context.settings
    sql = state["sql"]
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
        return {"sql": sql, "attempts": [attempt]}
    return {"sql": sql, "result": result}


def _after_run(state: AgentState, runtime: Runtime[AgentContext]) -> str:
    """Where to go after run_sql: answer, try again, or give up."""
    if "result" in state:
        return "summarize"
    if len(state["attempts"]) < runtime.context.settings.max_sql_attempts:
        return "repair_sql"
    return END  # no tries left: Pipeline.ask raises the last attempt's error


def _repair_sql(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Every failed attempt + its error → a new draft from the LLM."""
    draft = repair_sql(
        runtime.context.llm,
        schema_text=state["schema_text"],
        question=state["question"],
        failed=state["attempts"],
    )
    return {"sql": draft.sql, "explanation": draft.explanation}


def _summarize(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Rows → a plain-language answer, checked against the data (or the templated answer)."""
    result = state["result"]
    if runtime.context.summarize_answer:
        answer = summarize(runtime.context.llm, question=state["question"], result=result)
    else:
        answer = AnswerSummary(text=fallback_answer(result), from_llm=False)
    return {"answer": answer}


def _failed_attempt(sql: str, exc: Exception) -> SqlAttempt:
    """Record a fixable failure: a message for people, a detail for the LLM."""
    if isinstance(exc, UnsafeSqlError):
        return SqlAttempt(sql=sql, error_kind="unsafe", message=exc.reason, detail=exc.reason)
    if isinstance(exc, QueryExecutionError):
        return SqlAttempt(sql=sql, error_kind="db_error", message=str(exc), detail=exc.detail)
    return SqlAttempt(sql=sql, error_kind="timeout", message=str(exc), detail=str(exc))
