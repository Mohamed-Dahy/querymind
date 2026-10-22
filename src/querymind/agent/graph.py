"""The agent as a LangGraph graph: nodes (steps) joined by edges (what runs next).

    graph = build_graph()
    final = graph.invoke(
        {"question": ..., "schema_text": ..., "table_names": [...]},
        context=AgentContext(llm=llm, settings=settings, connect=connect_readonly),
    )

Today it's a straight line, the same steps as Phase 1:

    START → generate_sql → run_sql → summarize → END

Phase 2 adds branches: retry on SQL errors (2.2), sanity checks (2.3), clarifying questions
(2.4). Print the current diagram with `uv run python scripts/show_graph.py`.

Each node is a small function: it reads the state, calls one Phase 1 building block, and
returns only the fields it changed. Errors are not caught here yet: they end the run and reach
`Pipeline.ask` unchanged, as in Phase 1.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.runtime import Runtime

from querymind.agent.schemas import AnswerSummary
from querymind.agent.sql_generator import generate_sql
from querymind.agent.state import AgentContext, AgentState
from querymind.agent.summarizer import fallback_answer, summarize
from querymind.db.executor import execute_query
from querymind.safety.sql_validator import validate_sql

type AgentGraph = CompiledStateGraph[AgentState, AgentContext, AgentState, AgentState]


def build_graph() -> AgentGraph:
    """Wire the nodes together. Build it once and reuse it: a compiled graph is thread-safe."""
    graph = StateGraph(AgentState, context_schema=AgentContext)
    graph.add_node("generate_sql", _generate_sql)
    graph.add_node("run_sql", _run_sql)
    graph.add_node("summarize", _summarize)
    graph.add_edge(START, "generate_sql")
    graph.add_edge("generate_sql", "run_sql")
    graph.add_edge("run_sql", "summarize")
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
    """
    settings = runtime.context.settings
    query = validate_sql(
        state["sql"], allowed_tables=state["table_names"], max_rows=settings.max_rows
    )
    with runtime.context.connect(settings) as conn:
        result = execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds)
    return {"sql": query.sql, "result": result}


def _summarize(state: AgentState, runtime: Runtime[AgentContext]) -> AgentState:
    """Rows → a plain-language answer, checked against the data (or the templated answer)."""
    result = state["result"]
    if runtime.context.summarize_answer:
        answer = summarize(runtime.context.llm, question=state["question"], result=result)
    else:
        answer = AnswerSummary(text=fallback_answer(result), from_llm=False)
    return {"answer": answer}
