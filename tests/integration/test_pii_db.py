"""PII masking on the real database (5.4): the done-when, end to end.

Done when: masked output for a viewer, unmasked for an admin, in answers and in saved
conversations. Here on Northwind (its YAML layer flags customers.contact_name, phone, address...):
the same question as a viewer and as the workspace's admin, then the saved conversation and its
re-run. Also `SELECT *`: the masked positions must be Postgres's own column order.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import SqlDraft
from querymind.api.explorer import create_explorer
from querymind.api.main import create_app
from querymind.config import Settings
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.pii import MASK
from querymind.store.conversations import Owner, create_store
from tests.auth_helpers import signed_in, user_for

pytestmark = pytest.mark.integration

CONTACTS = SqlDraft(
    sql="SELECT company_name, contact_name FROM customers WHERE city = 'Berlin'",
    explanation="The Berlin customers and their contacts.",
    tables_used=["customers"],
)
QUESTION = {"question": "Who is the contact of our Berlin customer?"}


def client_as(
    settings: Settings, owner: Owner, role: str, *drafts: Any
) -> tuple[TestClient, FakeLLMProvider]:
    llm = FakeLLMProvider([*drafts, "Alfreds Futterkiste, contact hidden."])
    own = settings.model_copy(update={"schema_retrieval": "off"})  # not what this test is about
    app = create_app(
        pipeline=Pipeline(llm=llm, settings=own),
        explorer=create_explorer(own),
        store=create_store(own),
    )
    api = TestClient(signed_in(app, user_for(owner, role)), raise_server_exceptions=False)  # type: ignore[arg-type]
    return api, llm


def ask_and_reload(api: TestClient) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """The answer, its saved turn, and a re-run of it."""
    with api:
        answer = api.post("/ask", json=QUESTION).json()
        conversation = answer["conversation_id"]
        saved = api.get(f"/conversations/{conversation}").json()["saved_turns"][0]
        rerun = api.post(f"/conversations/{conversation}/turns/1/rerun").json()
    return answer, saved, rerun


def test_done_when_a_viewer_gets_masked_personal_data_and_an_admin_doesnt(
    settings: Settings, owner: Owner
) -> None:
    viewer, viewer_llm = client_as(settings, owner, "viewer", CONTACTS)
    admin, admin_llm = client_as(settings, owner, "admin", CONTACTS)

    answer, saved, rerun = ask_and_reload(viewer)
    boss, boss_saved, boss_rerun = ask_and_reload(admin)

    # The viewer: masked in the answer, the saved turn and the re-run.
    assert answer["rows"] == [["Alfreds Futterkiste", MASK]] and answer["masked_columns"] == [1]
    assert saved["reply"]["masked_columns"] == [1] and saved["reply"]["rows"] == []  # not stored
    assert "Maria Anders" not in str(saved)
    assert rerun["rows"] == [["Alfreds Futterkiste", MASK]] and rerun["masked_columns"] == [1]
    # Its LLM never saw the name: not in the SQL prompt's samples, not in the summary's rows.
    assert all("Maria Anders" not in call.prompt for call in viewer_llm.calls)
    # The admin, same question, same database: everything.
    assert boss["rows"] == [["Alfreds Futterkiste", "Maria Anders"]]
    assert boss["masked_columns"] == [] and boss_saved["reply"]["masked_columns"] == []
    assert boss_rerun["rows"] == [["Alfreds Futterkiste", "Maria Anders"]]
    assert any("Maria Anders" in call.prompt for call in admin_llm.calls)  # the summary's rows


def test_a_star_is_masked_at_postgres_own_column_positions(
    settings: Settings, owner: Owner
) -> None:
    star = SqlDraft(
        sql="SELECT * FROM customers WHERE customer_id = 'ALFKI'",
        explanation="One customer.",
        tables_used=["customers"],
    )
    viewer, _ = client_as(settings, owner, "viewer", star)
    with viewer:
        answer = viewer.post("/ask", json=QUESTION).json()

    columns, [row] = answer["columns"], answer["rows"]
    personal = {"contact_name", "contact_title", "address", "postal_code", "phone", "fax"}
    assert [columns[i] for i in answer["masked_columns"]] == [c for c in columns if c in personal]
    for column, value in zip(columns, row, strict=True):
        assert (value == MASK) == (column in personal and value is not None), column
    assert row[columns.index("company_name")] == "Alfreds Futterkiste"  # the rest is real
    assert row[columns.index("contact_name")] == MASK


def test_a_filter_on_a_personal_column_is_refused_for_a_viewer_only(
    settings: Settings, owner: Owner
) -> None:
    by_name = SqlDraft(
        sql="SELECT company_name FROM customers WHERE contact_name = 'Maria Anders'",
        explanation="Whose contact is Maria Anders.",
        tables_used=["customers"],
    )
    viewer, _ = client_as(settings, owner, "viewer", by_name, by_name, by_name)
    admin, _ = client_as(settings, owner, "admin", by_name)
    with viewer:
        refused = viewer.post("/ask", json=QUESTION)
    with admin:
        answered = admin.post("/ask", json=QUESTION)

    assert (refused.status_code, refused.json()["error"]) == (422, "unsafe_sql")
    assert "customers.contact_name is personal data" in refused.json()["message"]
    assert answered.status_code == 200 and answered.json()["rows"] == [["Alfreds Futterkiste"]]
