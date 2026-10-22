"""QueryMind chat UI. Start the API first, then:

    uv run streamlit run ui/app.py

A thin client: it only talks to the API over HTTP (querymind.client), never to the database or
the LLM directly. So the UI can't skip the safety checks, and it can be deployed separately.
"""

import streamlit as st

from querymind.api.schemas import AskResponse
from querymind.client import ApiError, ClientSettings, QueryMindClient

EXAMPLE_QUESTIONS = [
    "How many customers are there?",
    "What are the 5 most expensive products?",
    "Which employee handled the most orders?",
    "How many orders did each country place?",
    "What is the total sales amount per category?",
]

# What to tell the user for each API error code (the API's own message is shown below it).
ERROR_TITLES = {
    "unsafe_sql": "I couldn't answer that safely.",
    "invalid_request": "That question can't be sent.",
    "query_timeout": "The query took too long.",
    "query_failed": "The database couldn't run the query.",
    "db_unavailable": "The database isn't reachable right now.",
    "llm_unavailable": "The AI model is unavailable right now. Please try again.",
    "llm_bad_output": "The AI model gave an unusable answer. Please try rephrasing.",
    "api_unreachable": "The QueryMind API isn't reachable.",
    "api_timeout": "The API took too long to answer.",
}


@st.cache_resource
def get_client() -> QueryMindClient:
    """One HTTP client for the whole Streamlit server (cached across reruns and users)."""
    settings = ClientSettings()
    return QueryMindClient(settings.api_url, timeout_seconds=settings.api_timeout_seconds)


def main() -> None:
    st.set_page_config(page_title="QueryMind", page_icon="🔎", layout="wide")
    st.title("🔎 QueryMind")
    st.caption("Ask questions about the Northwind database in plain language.")

    client = get_client()
    question = render_sidebar(client)
    question = st.chat_input("Ask a question about the data") or question

    # Streamlit re-runs this whole script on every interaction, so the conversation lives in
    # session_state (kept per browser tab) and is redrawn each time.
    history: list[tuple[str, AskResponse | ApiError]] = st.session_state.setdefault("history", [])
    if question:
        with st.spinner("Thinking: writing SQL, checking it, running it…"):
            history.append((question, ask(client, question)))

    for asked, outcome in history:
        with st.chat_message("user"):
            st.write(asked)
        with st.chat_message("assistant"):
            if isinstance(outcome, ApiError):
                render_error(outcome)
            else:
                render_answer(outcome)


def render_sidebar(client: QueryMindClient) -> str | None:
    """API status, example questions (returns the one clicked, if any) and a reset button."""
    with st.sidebar:
        st.subheader("Status")
        try:
            health = client.health()
            st.success(f"API online · v{health.version}")
        except ApiError as exc:
            st.error(exc.message)

        st.subheader("Try asking")
        clicked = None
        for example in EXAMPLE_QUESTIONS:
            if st.button(example, width="stretch"):
                clicked = example

        if st.button("Clear conversation", type="secondary"):
            st.session_state["history"] = []
        return clicked


def ask(client: QueryMindClient, question: str) -> AskResponse | ApiError:
    try:
        return client.ask(question)
    except ApiError as exc:
        return exc


def render_answer(response: AskResponse) -> None:
    """The answer first, then the table, then the SQL as evidence."""
    st.markdown(response.answer)
    if not response.answer_from_llm:
        st.caption(
            ":information_source: Built directly from the data (the AI's wording wasn't used)."
        )

    if response.rows:
        st.dataframe(table_rows(response), hide_index=True)
    note = f"{response.row_count} row{'s' if response.row_count != 1 else ''}"
    if response.truncated:
        st.warning(f"Showing the first {note}: more rows matched than the row limit allows.")
    else:
        st.caption(note)

    with st.expander("Show SQL"):
        st.code(response.sql, language="sql")
        st.caption(response.explanation)


def render_error(error: ApiError) -> None:
    st.error(ERROR_TITLES.get(error.code, "Something went wrong."))
    st.caption(error.message)


def table_rows(response: AskResponse) -> list[dict[str, object]]:
    """Rows as dicts for st.dataframe. Repeated column names (e.g. two "name") get a suffix,
    because a table can't have two columns with the same name."""
    seen: dict[str, int] = {}
    columns = []
    for column in response.columns:
        seen[column] = seen.get(column, 0) + 1
        columns.append(column if seen[column] == 1 else f"{column}_{seen[column]}")
    return [dict(zip(columns, row, strict=True)) for row in response.rows]


main()
