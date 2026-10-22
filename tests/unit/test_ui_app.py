"""The Streamlit page, run headless with AppTest. The API calls are faked; no server needed.

AppTest runs ui/app.py like a browser would, lets us type in the chat box or click buttons,
and shows what got drawn (markdown, tables, errors, the SQL expander).
"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from querymind.api.schemas import (
    AskResponse,
    ClarificationResponse,
    HealthResponse,
    RefusalResponse,
    ResultWarning,
)
from querymind.client import ApiError, QueryMindClient

APP = str(Path(__file__).parents[2] / "ui" / "app.py")

ANSWER = AskResponse(
    question="Top products?",
    answer="Côte de Blaye is the most expensive at 263.5.",
    answer_from_llm=True,
    sql="SELECT product_name, unit_price FROM products ORDER BY unit_price DESC LIMIT 2",
    explanation="Lists products by price.",
    columns=["product_name", "unit_price"],
    rows=[["Côte de Blaye", 263.5], ["Thüringer Rostbratwurst", 123.79]],
    row_count=2,
    truncated=False,
)


DOUBLED = ResultWarning(code="duplicate_rows", message="Some rows appear more than once.")


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the client's HTTP calls. Returns the list of questions the UI sent."""
    asked: list[str] = []

    def ask(
        self: QueryMindClient, question: str
    ) -> AskResponse | ClarificationResponse | RefusalResponse:
        asked.append(question)
        if question == "break it":
            raise ApiError("unsafe_sql", "Only SELECT queries are allowed.", status=422)
        if question == "db down":
            raise ApiError("db_unavailable", "Could not connect to the database.", status=503)
        if question == "Best customers?":
            return ClarificationResponse(
                question=question,
                clarifying_question="Best by what?",
                options=["by total revenue", "by number of orders"],
            )
        if question == "Delete all orders":
            return RefusalResponse(question=question, message="I can only read data.")
        if question == "doubled":
            return ANSWER.model_copy(update={"warnings": [DOUBLED]})
        return ANSWER

    monkeypatch.setattr(QueryMindClient, "ask", ask)
    monkeypatch.setattr(
        QueryMindClient, "health", lambda self: HealthResponse(status="ok", version="0.1.0")
    )
    return asked


def run_app() -> AppTest:
    app = AppTest.from_file(APP, default_timeout=10)
    app.run()
    assert not app.exception
    return app


def test_page_loads(fake_api: list[str]) -> None:
    app = run_app()
    assert app.title[0].value == "🔎 QueryMind"
    assert "API online · v0.1.0" in app.sidebar.success[0].value
    assert fake_api == []  # nothing asked until the user asks


def test_question_shows_answer_table_and_sql(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("Top products?").run()

    assert fake_api == ["Top products?"]
    assert any(m.value == ANSWER.answer for m in app.markdown)
    [table] = app.dataframe
    assert list(table.value.columns) == ["product_name", "unit_price"]
    assert table.value.iloc[0]["product_name"] == "Côte de Blaye"
    assert app.expander[0].label == "Show SQL"
    assert app.code[0].value == ANSWER.sql


def test_result_warnings_are_shown(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("doubled").run()
    assert [w.value for w in app.warning] == ["Some rows appear more than once."]
    app.chat_input[0].set_value("Top products?").run()
    assert len(app.warning) == 1  # the clean answer adds none


def test_example_button_asks_that_question(fake_api: list[str]) -> None:
    app = run_app()
    app.sidebar.button[1].click().run()  # "What are the 5 most expensive products?"
    assert fake_api == ["What are the 5 most expensive products?"]


def test_api_errors_are_shown_plainly(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("break it").run()
    assert app.error[0].value == "I couldn't answer that safely."
    assert any(c.value == "Only SELECT queries are allowed." for c in app.caption)
    assert not app.exception


def test_database_down_has_its_own_title(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("db down").run()
    assert app.error[0].value == "The database isn't reachable right now."


def test_conversation_is_kept_between_questions(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("Top products?").run()
    app.chat_input[0].set_value("break it").run()
    assert len(app.chat_message) == 4  # 2 questions + 2 replies
    assert len(app.dataframe) == 1  # the first answer's table is still drawn


def test_api_down_is_shown_in_the_sidebar(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(self: QueryMindClient) -> HealthResponse:
        raise ApiError("api_unreachable", "Can't reach the QueryMind API. Is it running?")

    monkeypatch.setattr(QueryMindClient, "health", down)
    app = run_app()
    assert "Is it running?" in app.sidebar.error[0].value


def test_a_clarification_option_asks_again_with_that_meaning(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("Best customers?").run()
    assert any(m.value == "Best by what?" for m in app.markdown)
    app.button(key="clarify-0-0").click().run()  # "by total revenue"
    assert fake_api == ["Best customers?", "Best customers? (by total revenue)"]
    assert len(app.chat_message) == 4  # question, options, clarified question, answer
    assert not app.exception


def test_a_refusal_is_shown_as_information(fake_api: list[str]) -> None:
    app = run_app()
    app.chat_input[0].set_value("Delete all orders").run()
    assert [i.value for i in app.info] == ["I can only read data."]
    assert not app.error  # a refusal is not an error
