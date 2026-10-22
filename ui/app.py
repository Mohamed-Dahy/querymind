"""QueryMind chat UI. Start the API first, then:

    uv run streamlit run ui/app.py

A thin client: it only talks to the API over HTTP (querymind.client), never to the database or
the LLM directly. So the UI can't skip the safety checks, and it can be deployed separately.
"""

from typing import Literal

import streamlit as st

from querymind.api.schemas import AskResponse, ClarificationResponse, RefusalResponse
from querymind.client import ApiError, ClientSettings, QueryMindClient

EXAMPLE_QUESTIONS = [
    "How many customers are there?",
    "What are the 5 most expensive products?",
    "Which employee handled the most orders?",
    "How many orders did each country place?",
    "What is the total sales amount per category?",
]

# Everything the API can send back for a question (or the error we got instead).
type Reply = AskResponse | ClarificationResponse | RefusalResponse | ApiError

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
    # A clicked clarification option (see render_clarification) arrives as a "pending" question.
    question = (
        st.chat_input("Ask a question about the data")
        or question
        or st.session_state.pop("pending", None)
    )

    # Streamlit re-runs this whole script on every interaction, so the conversation lives in
    # session_state (kept per browser tab) and is redrawn each time.
    history: list[tuple[str, Reply]] = st.session_state.setdefault("history", [])
    if question:
        with st.spinner("Thinking: writing SQL, checking it, running it…"):
            history.append((question, ask(client, question)))

    for index, (asked, outcome) in enumerate(history):
        with st.chat_message("user"):
            st.write(asked)
        with st.chat_message("assistant"):
            if isinstance(outcome, ApiError):
                render_error(outcome)
            elif isinstance(outcome, ClarificationResponse):
                render_clarification(outcome, index)
            elif isinstance(outcome, RefusalResponse):
                st.info(outcome.message, icon=":material/block:")
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


def ask(client: QueryMindClient, question: str) -> Reply:
    try:
        return client.ask(question)
    except ApiError as exc:
        return exc


def render_answer(response: AskResponse) -> None:
    """The answer first, then the table, then the SQL as evidence."""
    st.markdown(response.answer)
    render_confidence(response)
    for warning in response.warnings:
        st.warning(warning.message, icon=":material/warning:")
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


# Streamlit's own color names for the badge (a Literal type, so mypy checks the spelling).
CONFIDENCE_COLORS: dict[str, Literal["green", "orange", "red"]] = {
    "high": "green",
    "medium": "orange",
    "low": "red",
}


def render_confidence(response: AskResponse) -> None:
    """A colored badge (High / Medium / Low), and why, when it isn't high."""
    st.badge(
        f"{response.confidence.title()} confidence",
        color=CONFIDENCE_COLORS[response.confidence],
        icon=":material/verified:" if response.confidence == "high" else ":material/info:",
    )
    if response.confidence_reasons:
        st.caption(" ".join(response.confidence_reasons))


def render_clarification(response: ClarificationResponse, index: int) -> None:
    """The question back, with one button per meaning. A click asks the question again with
    that meaning added, e.g. "Who are our best customers? (by total revenue)"."""
    st.markdown(response.clarifying_question)
    for number, option in enumerate(response.options):
        # Keys must be unique on the page: this answer's position + the option's.
        if st.button(option, key=f"clarify-{index}-{number}"):
            st.session_state["pending"] = f"{response.question} ({option})"
            st.rerun()  # start over, so main() picks the pending question up
    st.caption("Or type your question again with more detail.")


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
