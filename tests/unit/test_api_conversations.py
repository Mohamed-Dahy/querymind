"""The conversation endpoints with a fake store and a fake pipeline. No DB, no LLM."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.agent.events import StepEvent
from querymind.api.main import create_app
from querymind.db.executor import QueryResult
from querymind.safety.errors import UnsafeSqlError
from querymind.store.conversations import Conversation, ConversationSummary, SavedTurn
from querymind.store.errors import (
    ConversationNotFoundError,
    StoreUnavailableError,
    TurnNotFoundError,
)

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ID = uuid4()
SQL = "SELECT company_name FROM customers LIMIT 1001"
ANSWER_REPLY = {
    "kind": "answer",
    "question": "Top customers?",
    "conversation_id": str(ID),
    "answer": "Two customers lead.",
    "answer_from_llm": True,
    "sql": SQL,
    "explanation": "Lists customers.",
    "columns": ["company_name"],
    "row_count": 2,
    "truncated": False,
    "warnings": [],
    "confidence": "high",
    "confidence_reasons": [],
}
ANSWER = SavedTurn(
    n=1,
    question="Top customers?",
    kind="answer",
    sql=SQL,
    reply=ANSWER_REPLY,
    trace=[StepEvent(node="generate_sql", status="started").model_dump(mode="json")],
    created_at=NOW,
)
ASK_BACK = SavedTurn(
    n=2,
    question="Best ones?",
    kind="clarification",
    sql=None,
    reply={
        "kind": "clarification",
        "question": "Best ones?",
        "conversation_id": str(ID),
        "clarifying_question": "By what?",
        "options": ["orders", "revenue"],
    },
    trace=[],
    created_at=NOW,
)
SUMMARY = ConversationSummary(
    id=ID, title="Top customers?", pinned=False, created_at=NOW, updated_at=NOW, turns=2
)
FRESH = QueryResult(
    columns=["company_name"], rows=[["Alfreds"]], row_count=1, truncated=False, duration_ms=1
)


class FakeStore:
    """One conversation, or a failure. Notes what it was asked."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.searches: list[tuple[str | None, int, int]] = []
        self.updates: list[dict[str, Any]] = []
        self.deleted: list[UUID] = []

    def _check(self, conversation_id: UUID | None = None) -> None:
        if self.error is not None:
            raise self.error
        if conversation_id is not None and conversation_id != ID:
            raise ConversationNotFoundError("This conversation doesn't exist.")

    def list_conversations(
        self, *, search: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[ConversationSummary], bool]:
        self._check()
        self.searches.append((search, limit, offset))
        return [SUMMARY], True

    def get(self, conversation_id: UUID) -> Conversation:
        self._check(conversation_id)
        return Conversation(**SUMMARY.model_dump(), saved_turns=[ANSWER, ASK_BACK])

    def get_turn(self, conversation_id: UUID, n: int) -> SavedTurn:
        self._check(conversation_id)
        for turn in (ANSWER, ASK_BACK):
            if turn.n == n:
                return turn
        raise TurnNotFoundError("This turn doesn't exist.")

    def update(
        self, conversation_id: UUID, *, title: str | None = None, pinned: bool | None = None
    ) -> ConversationSummary:
        self._check(conversation_id)
        self.updates.append({"title": title, "pinned": pinned})
        return SUMMARY.model_copy(update={"title": title or SUMMARY.title})

    def delete(self, conversation_id: UUID) -> None:
        self._check(conversation_id)
        self.deleted.append(conversation_id)


class FakePipeline:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.forgotten: list[str] = []
        self.reruns: list[tuple[str, str, str | None]] = []

    def forget(self, conversation_id: str) -> None:
        self.forgotten.append(conversation_id)

    def rerun(self, question: str, sql: str, *, conversation_id: str | None = None) -> QueryResult:
        self.reruns.append((question, sql, conversation_id))
        if self.error is not None:
            raise self.error
        return FRESH


def client(store: FakeStore | None, pipeline: FakePipeline | None = None) -> TestClient:
    app = create_app(pipeline=pipeline or FakePipeline(), store=store)  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


# --- List ----------------------------------------------------------------------------------------


def test_list_gives_summaries_and_whether_there_is_more() -> None:
    with client(FakeStore()) as api:
        body = api.get("/conversations").json()

    assert body["has_more"] is True
    assert body["conversations"][0]["id"] == str(ID)
    assert body["conversations"][0]["turns"] == 2


def test_list_passes_search_and_paging_on() -> None:
    store = FakeStore()

    with client(store) as api:
        api.get("/conversations", params={"q": "sales", "limit": 10, "offset": 20})
        api.get("/conversations", params={"q": ""})

    assert store.searches == [("sales", 10, 20), (None, 50, 0)]


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"offset": -1}, {"q": "x" * 201}])
def test_list_refuses_silly_paging(params: dict[str, Any]) -> None:
    with client(FakeStore()) as api:
        response = api.get("/conversations", params=params)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


# --- Read ----------------------------------------------------------------------------------------


def test_get_gives_each_turn_in_the_api_s_own_reply_shape() -> None:
    with client(FakeStore()) as api:
        body = api.get(f"/conversations/{ID}").json()

    assert body["title"] == "Top customers?"
    answer, asked = body["saved_turns"]
    assert answer["n"] == 1
    assert answer["reply"]["answer"] == "Two customers lead."
    assert answer["reply"]["row_count"] == 2
    assert answer["reply"]["rows"] == []  # not stored: re-run for fresh rows
    assert answer["trace"][0]["type"] == "step"
    assert asked["reply"]["kind"] == "clarification"
    assert "rows" not in asked["reply"]


def test_a_missing_conversation_is_404() -> None:
    with client(FakeStore()) as api:
        response = api.get(f"/conversations/{uuid4()}")

    assert response.status_code == 404
    assert response.json() == {
        "error": "conversation_not_found",
        "message": "This conversation doesn't exist.",
    }


@pytest.mark.parametrize("method", ["get", "delete"])
def test_a_bad_id_is_422(method: str) -> None:
    with client(FakeStore()) as api:
        response = getattr(api, method)("/conversations/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


# --- Update --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"title": "  Weekly numbers  "}, {"title": "Weekly numbers", "pinned": None}),
        ({"pinned": True}, {"title": None, "pinned": True}),
        ({"pinned": False}, {"title": None, "pinned": False}),
        ({"title": "T", "pinned": True}, {"title": "T", "pinned": True}),
    ],
)
def test_patch_renames_and_pins(body: dict[str, Any], expected: dict[str, Any]) -> None:
    store = FakeStore()

    with client(store) as api:
        response = api.patch(f"/conversations/{ID}", json=body)

    assert response.status_code == 200
    assert store.updates == [expected]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"title": "   "},
        {"title": "x" * 201},
        {"sql": "DROP TABLE orders"},
        {"title": "T", "created_at": "2020-01-01"},
    ],
)
def test_patch_refuses_anything_else(body: dict[str, Any]) -> None:
    store = FakeStore()

    with client(store) as api:
        response = api.patch(f"/conversations/{ID}", json=body)

    assert response.status_code == 422
    assert store.updates == []


def test_patching_a_missing_conversation_is_404() -> None:
    with client(FakeStore()) as api:
        response = api.patch(f"/conversations/{uuid4()}", json={"pinned": True})

    assert response.status_code == 404


# --- Delete --------------------------------------------------------------------------------------


def test_delete_also_clears_the_agent_s_memory() -> None:
    store = FakeStore()
    pipeline = FakePipeline()

    with client(store, pipeline) as api:
        response = api.delete(f"/conversations/{ID}")

    assert response.status_code == 204
    assert response.content == b""
    assert store.deleted == [ID]
    assert pipeline.forgotten == [str(ID)]


def test_deleting_a_missing_conversation_is_404_and_touches_nothing() -> None:
    pipeline = FakePipeline()

    with client(FakeStore(), pipeline) as api:
        response = api.delete(f"/conversations/{uuid4()}")

    assert response.status_code == 404
    assert pipeline.forgotten == []


# --- Re-run --------------------------------------------------------------------------------------


def test_rerun_runs_the_stored_sql_and_gives_fresh_rows() -> None:
    pipeline = FakePipeline()

    with client(FakeStore(), pipeline) as api:
        body = api.post(f"/conversations/{ID}/turns/1/rerun").json()

    assert pipeline.reruns == [("Top customers?", SQL, str(ID))]
    assert body["rows"] == [["Alfreds"]]
    assert body["columns"] == ["company_name"]
    assert body["row_count"] == 1
    assert body["truncated"] is False
    assert body["ran_at"]


def test_rerun_ignores_anything_the_client_sends() -> None:
    pipeline = FakePipeline()

    with client(FakeStore(), pipeline) as api:
        api.post(f"/conversations/{ID}/turns/1/rerun", json={"sql": "DROP TABLE orders"})

    assert pipeline.reruns[0][1] == SQL


@pytest.mark.parametrize("n", [2, 99])
def test_rerun_of_a_turn_without_sql_or_a_missing_turn_is_404(n: int) -> None:
    pipeline = FakePipeline()

    with client(FakeStore(), pipeline) as api:
        response = api.post(f"/conversations/{ID}/turns/{n}/rerun")

    assert response.status_code == 404
    assert response.json()["error"] == "turn_not_found"
    assert pipeline.reruns == []


def test_rerun_with_a_bad_turn_number_is_422() -> None:
    with client(FakeStore()) as api:
        response = api.post(f"/conversations/{ID}/turns/0/rerun")

    assert response.status_code == 422


def test_rerun_refused_by_the_validator_is_a_clean_error() -> None:
    pipeline = FakePipeline(error=UnsafeSqlError("Only SELECT queries are allowed."))

    with client(FakeStore(), pipeline) as api:
        response = api.post(f"/conversations/{ID}/turns/1/rerun")

    assert response.status_code == 422
    assert response.json()["error"] == "unsafe_sql"


# --- When history isn't there --------------------------------------------------------------------

ROUTES = [
    ("get", "/conversations", None),
    ("get", f"/conversations/{ID}", None),
    ("patch", f"/conversations/{ID}", {"pinned": True}),
    ("delete", f"/conversations/{ID}", None),
    ("post", f"/conversations/{ID}/turns/1/rerun", None),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_with_history_off_every_route_says_so(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    with client(None) as api:
        response = api.request(method, path, json=body)

    assert response.status_code == 503
    assert response.json()["error"] == "history_unavailable"


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_failing_store_is_a_clean_503(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    store = FakeStore(
        error=StoreUnavailableError("Saved conversations aren't available right now.")
    )

    with client(store) as api:
        response = api.request(method, path, json=body)

    assert response.status_code == 503
    assert response.json() == {
        "error": "history_unavailable",
        "message": "Saved conversations aren't available right now.",
    }


# --- The docs ------------------------------------------------------------------------------------


def test_no_route_takes_sql_from_the_client() -> None:
    spec = create_app(pipeline=FakePipeline()).openapi()
    schemas = spec["components"]["schemas"]

    rerun = spec["paths"]["/conversations/{conversation_id}/turns/{n}/rerun"]["post"]
    assert "requestBody" not in rerun
    for path, operations in spec["paths"].items():
        for operation in operations.values():
            body = operation.get("requestBody")
            if body is None:
                continue
            reference = body["content"]["application/json"]["schema"]["$ref"]
            fields = schemas[reference.rsplit("/", 1)[-1]]["properties"]
            assert "sql" not in fields, path
