"""The saved-question endpoints with a fake store. No DB, no LLM."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.main import create_app
from querymind.store.conversations import Owner, SavedQuestion
from querymind.store.errors import StoreUnavailableError
from tests.auth_helpers import TEST_OWNER, signed_in

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)


class FakeStore:
    """Saved questions in a list, like the real table: one row per wording."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.saved: list[SavedQuestion] = []
        self.owners: list[Owner] = []  # who each call was for

    def _check(self, owner: Owner) -> None:
        self.owners.append(owner)
        if self.error is not None:
            raise self.error

    def list_saved_questions(self, *, owner: Owner) -> list[SavedQuestion]:
        self._check(owner)
        return list(self.saved)

    def add_saved_question(self, question: str, *, owner: Owner) -> SavedQuestion:
        self._check(owner)
        for one in self.saved:
            if one.question == question:
                return one
        added = SavedQuestion(id=uuid4(), question=question, created_at=NOW)
        self.saved.insert(0, added)
        return added

    def remove_saved_question(self, saved_id: UUID, *, owner: Owner) -> None:
        self._check(owner)
        self.saved = [one for one in self.saved if one.id != saved_id]


def client(store: FakeStore | None) -> TestClient:
    app = signed_in(create_app(pipeline=object(), store=store))  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def test_a_saved_question_is_in_the_list_with_its_words() -> None:
    with client(FakeStore()) as api:
        saved = api.post("/saved-questions", json={"question": "Sales this week?"})
        listed = api.get("/saved-questions")

    assert saved.status_code == 200
    assert saved.json()["question"] == "Sales this week?"
    assert listed.json()["questions"] == [saved.json()]


def test_saving_twice_gives_the_same_row() -> None:
    with client(FakeStore()) as api:
        first = api.post("/saved-questions", json={"question": "Sales this week?"}).json()
        second = api.post("/saved-questions", json={"question": "  Sales this week?  "}).json()
        listed = api.get("/saved-questions").json()

    assert second == first  # the spaces around it don't make it another question
    assert len(listed["questions"]) == 1


def test_removing_takes_it_off_the_list_and_twice_is_fine() -> None:
    with client(FakeStore()) as api:
        saved = api.post("/saved-questions", json={"question": "Sales this week?"}).json()
        first = api.delete(f"/saved-questions/{saved['id']}")
        second = api.delete(f"/saved-questions/{saved['id']}")
        listed = api.get("/saved-questions").json()

    assert (first.status_code, second.status_code) == (204, 204)
    assert listed["questions"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": "x" * 1001},  # more than a question may be, so it could never be asked again
        {"question": "Sales?", "sql": "SELECT 1"},  # only the words are kept
    ],
)
def test_saving_refuses_a_bad_body(body: dict[str, str]) -> None:
    with client(FakeStore()) as api:
        response = api.post("/saved-questions", json=body)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_removing_refuses_a_bad_id() -> None:
    with client(FakeStore()) as api:
        response = api.delete("/saved-questions/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


@pytest.mark.parametrize(
    "store",
    [None, FakeStore(StoreUnavailableError("down"))],
    ids=["history off", "database down"],
)
def test_without_the_store_each_route_says_so(store: FakeStore | None) -> None:
    with client(store) as api:
        responses = [
            api.get("/saved-questions"),
            api.post("/saved-questions", json={"question": "Sales?"}),
            api.delete(f"/saved-questions/{uuid4()}"),
        ]

    assert [response.status_code for response in responses] == [503, 503, 503]
    assert {response.json()["error"] for response in responses} == {"history_unavailable"}


def test_saved_questions_are_the_logged_in_users() -> None:
    store = FakeStore()
    with client(store) as api:
        saved = api.post("/saved-questions", json={"question": "Sales this week?"}).json()
        api.get("/saved-questions")
        api.delete(f"/saved-questions/{saved['id']}")

    assert store.owners == [TEST_OWNER] * 3
