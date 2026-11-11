"""Questions on a workspace's own databases (5.11): the pipeline registry and the routes that use
it. Each database has its own pipeline; only the workspace's own key answers there; a follow-up
stays on its conversation's database; re-runs and deletes go to the right pipeline."""

import json
from collections.abc import Generator
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, SecretStr

from querymind.agent.events import AgentEvent, Finished
from querymind.agent.registry import NoOwnLLM, PipelineRegistry, database_settings
from querymind.agent.schemas import AgentReply
from querymind.api import connections as connection_routes
from querymind.api.main import create_app
from querymind.db.executor import QueryResult
from querymind.db.remote import ConnectionSpec
from querymind.llm import LLMProvider, LLMProviderError
from querymind.llm.workspace import WorkspaceLLMs
from querymind.secrets.errors import SecretUnreadableError
from querymind.store.connections import ConnectionRecord
from querymind.store.errors import ConnectionNotFoundError, ConnectionUnreadableError
from tests.auth_helpers import OTHER_USER, TEST_USER, signed_in
from tests.unit.test_api import RESULT
from tests.unit.test_api_connections import MemoryConnections
from tests.unit.test_conversation import pipeline_with
from tests.unit.test_llm_keys import GROQ_KEY, MemoryAudit, MemoryKeys, build

SPEC = ConnectionSpec(host="db.example.com", dbname="sales", user="reader")
ROWS = QueryResult(columns=["n"], rows=[[1]], row_count=1, truncated=False, duration_ms=1)


class FakeDatabasePipeline:
    """Stands in for one database's Pipeline: records what it was asked."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.asked: list[tuple[str, LLMProvider | None]] = []
        self.reruns: list[str] = []
        self.forgotten: list[str] = []

    def ask(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> AgentReply:
        self.asked.append((question, llm))
        return RESULT

    def stream(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> Generator[AgentEvent | Finished]:
        self.asked.append((question, llm))
        yield Finished(RESULT)

    def rerun(self, question: str, sql: str, *, conversation_id: str | None = None) -> QueryResult:
        self.reruns.append(sql)
        return ROWS

    def forget(self, conversation_id: str) -> None:
        self.forgotten.append(conversation_id)


class MemoryConversations:
    """Stands in for ConversationStore: which conversation is about which database."""

    def __init__(self) -> None:
        self.databases: dict[UUID, UUID | None] = {}
        self.saved: list[dict[str, object]] = []
        self.deleted: list[UUID] = []

    def database_of(self, conversation_id: UUID, *, owner: object) -> tuple[bool, UUID | None]:
        if conversation_id not in self.databases:
            return False, None
        return True, self.databases[conversation_id]

    def save_turn(self, conversation_id: UUID, **turn: object) -> int:
        database = turn.get("connection_id")
        self.databases.setdefault(conversation_id, database if isinstance(database, UUID) else None)
        self.saved.append({"conversation_id": conversation_id, **turn})
        return len(self.saved)

    def get_turn(self, conversation_id: UUID, n: int, *, owner: object) -> object:
        return type("Turn", (), {"question": "Top customers?", "sql": "SELECT 1"})()

    def delete(self, conversation_id: UUID, *, owner: object) -> None:
        self.deleted.append(conversation_id)


class Setup:
    """An app with a demo pipeline, saved databases and a registry built of fakes."""

    def __init__(self, *, own_key: bool = True) -> None:
        self.demo = FakeDatabasePipeline("demo")
        self.connections = MemoryConnections()
        self.keys = MemoryKeys()
        if own_key:
            self.keys.save(
                TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY)
            )
        self.built: dict[UUID, FakeDatabasePipeline] = {}
        self.conversations = MemoryConversations()
        self.audit = MemoryAudit()
        self.sales = self.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("pw"))
        self.registry = PipelineRegistry(
            self.demo,  # type: ignore[arg-type]
            self.connections,  # type: ignore[arg-type]
            settings=pipeline_with()[0]._settings,
            build=self._build,  # type: ignore[arg-type]
        )
        self.app = signed_in(
            create_app(
                pipeline=self.demo,
                store=self.conversations,  # type: ignore[arg-type]
                audit=self.audit,  # type: ignore[arg-type]
                connections=self.connections,  # type: ignore[arg-type]
                pipelines=self.registry,
                llm_keys=self.keys,  # type: ignore[arg-type]
                workspace_llms=WorkspaceLLMs(self.keys, self.keys, build=build),  # type: ignore[arg-type]
            )
        )

    def _build(
        self, record: ConnectionRecord, password: SecretStr, dictionary: object = None
    ) -> FakeDatabasePipeline:
        pipeline = FakeDatabasePipeline(record.name)
        self.built[record.id] = pipeline
        return pipeline

    def ask(self, client: TestClient, **body: object) -> object:
        return client.post("/ask", json={"question": "Top customers?"} | body)


# --- The registry ---------------------------------------------------------------------------------


def test_no_database_means_the_demo_pipeline() -> None:
    setup = Setup()

    assert setup.registry.for_database(TEST_USER.workspace_id, None) is setup.registry.demo
    assert setup.registry.demo is setup.demo  # type: ignore[comparison-overlap]


def test_each_database_gets_one_pipeline_kept_until_forgotten() -> None:
    setup = Setup()
    first = setup.registry.for_database(TEST_USER.workspace_id, setup.sales.id)

    assert setup.registry.for_database(TEST_USER.workspace_id, setup.sales.id) is first
    setup.registry.forget(setup.sales.id)
    assert setup.registry.for_database(TEST_USER.workspace_id, setup.sales.id) is not first


def test_another_workspaces_database_is_not_found() -> None:
    setup = Setup()

    with pytest.raises(ConnectionNotFoundError):
        setup.registry.for_database(OTHER_USER.workspace_id, setup.sales.id)


def test_an_unreadable_password_says_so_in_database_words(monkeypatch: pytest.MonkeyPatch) -> None:
    setup = Setup()

    def unreadable(*_args: object) -> SecretStr:
        raise SecretUnreadableError("A saved secret can't be read: save it again.")

    monkeypatch.setattr(setup.connections, "password", unreadable)

    with pytest.raises(ConnectionUnreadableError, match="database's saved password"):
        setup.registry.for_database(TEST_USER.workspace_id, setup.sales.id)


def test_a_user_databases_own_llm_refuses_so_our_key_never_answers_there() -> None:
    class Answer(BaseModel):
        n: int

    with pytest.raises(LLMProviderError):
        NoOwnLLM().generate_text("q")
    with pytest.raises(LLMProviderError):
        NoOwnLLM().generate_structured("q", Answer)


def test_a_user_database_gets_no_demo_semantic_layers_and_no_retrieval() -> None:
    settings = database_settings(pipeline_with()[0]._settings)

    assert settings.semantic_layer_paths == []
    assert settings.schema_retrieval == "off"


# --- The routes -----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_a_question_on_an_own_database_runs_on_its_pipeline_with_the_own_key(path: str) -> None:
    setup = Setup()
    with TestClient(setup.app) as client:
        response = client.post(
            path, json={"question": "Top customers?", "connection_id": str(setup.sales.id)}
        )

    assert response.status_code == 200
    pipeline = setup.built[setup.sales.id]
    [(_, llm)] = pipeline.asked
    assert llm is not None and llm.name == "groq"
    assert setup.demo.asked == []
    [saved] = setup.conversations.saved
    assert saved["connection_id"] == setup.sales.id
    assert setup.audit.lines[-1].connection_id == setup.sales.id


@pytest.mark.parametrize("path", ["/ask", "/ask/stream"])
def test_an_own_database_without_an_own_key_is_refused_and_audited(path: str) -> None:
    setup = Setup(own_key=False)
    with TestClient(setup.app) as client:
        response = client.post(
            path, json={"question": "Top customers?", "connection_id": str(setup.sales.id)}
        )

    error: dict[str, Any] = (
        response.json() if path == "/ask" else _last_event(response.text)["error"]
    )
    assert error["error"] == "llm_key_required"
    assert setup.built == {}  # nothing built, nothing connected
    [line] = setup.audit.lines
    assert (line.outcome, line.connection_id) == ("llm_key_required", setup.sales.id)


def test_without_a_connection_the_demo_answers_as_before() -> None:
    setup = Setup(own_key=False)
    with TestClient(setup.app) as client:
        assert setup.ask(client).status_code == 200  # type: ignore[attr-defined]

    assert len(setup.demo.asked) == 1 and setup.built == {}


def test_a_follow_up_stays_on_its_conversations_database() -> None:
    setup = Setup()
    with TestClient(setup.app) as client:
        first = setup.ask(client, connection_id=str(setup.sales.id)).json()  # type: ignore[attr-defined]
        # The follow-up names no database: it goes where its conversation is.
        setup.ask(client, conversation_id=first["conversation_id"])
        # Naming another of the workspace's databases for it is refused.
        other = setup.connections.save(TEST_USER.workspace_id, "Other", SPEC, SecretStr("pw"))
        refused = setup.ask(
            client, conversation_id=first["conversation_id"], connection_id=str(other.id)
        )

    assert len(setup.built[setup.sales.id].asked) == 2
    assert refused.status_code == 409  # type: ignore[attr-defined]
    assert refused.json()["error"] == "conversation_other_database"  # type: ignore[attr-defined]


def test_another_workspaces_database_is_a_404() -> None:
    setup = Setup()
    foreign = setup.connections.save(OTHER_USER.workspace_id, "Theirs", SPEC, SecretStr("pw"))
    with TestClient(setup.app) as client:
        response = setup.ask(client, connection_id=str(foreign.id))

    assert response.status_code == 404  # type: ignore[attr-defined]
    assert response.json()["error"] == "connection_not_found"  # type: ignore[attr-defined]


def test_a_rerun_goes_to_its_conversations_database_without_a_key() -> None:
    setup = Setup(own_key=False)  # a re-run makes no LLM call
    conversation = uuid4()
    setup.conversations.databases[conversation] = setup.sales.id
    with TestClient(setup.app) as client:
        response = client.post(f"/conversations/{conversation}/turns/1/rerun")

    assert response.status_code == 200
    assert setup.built[setup.sales.id].reruns == ["SELECT 1"]
    assert setup.audit.lines[-1].connection_id == setup.sales.id


def test_deleting_a_conversation_forgets_it_in_its_own_database() -> None:
    setup = Setup()
    with TestClient(setup.app) as client:
        first = setup.ask(client, connection_id=str(setup.sales.id)).json()  # type: ignore[attr-defined]
        assert client.delete(f"/conversations/{first['conversation_id']}").status_code == 204

    assert setup.built[setup.sales.id].forgotten == [first["conversation_id"]]
    assert setup.demo.forgotten == []


def test_deleting_a_database_drops_its_pipeline() -> None:
    setup = Setup()
    with TestClient(setup.app) as client:
        setup.ask(client, connection_id=str(setup.sales.id))
        built = setup.built[setup.sales.id]
        assert client.delete(f"/connections/{setup.sales.id}").status_code == 204

    assert setup.registry._cache == {}  # its pipeline, holding its password, is gone
    assert built.asked  # it was the one that answered before


def _last_event(text: str) -> dict[str, Any]:
    events: list[dict[str, Any]] = [
        json.loads(line[5:]) for line in text.splitlines() if line.startswith("data:")
    ]
    return events[-1]


def test_a_question_on_a_saved_private_address_is_refused_in_normal_mode() -> None:
    """The real registry (default builder, no development mode): the SSRF guard runs when the
    question connects, not only when the database was saved."""
    setup = Setup()
    local = setup.connections.save(
        TEST_USER.workspace_id,
        "Local",
        ConnectionSpec(host="127.0.0.1", dbname="sales", user="reader"),
        SecretStr("pw"),
    )
    with TestClient(setup.app) as client:
        # The lifespan set the fake-building registry: swap in a real one.
        setup.app.state.pipelines = PipelineRegistry(
            setup.demo,  # type: ignore[arg-type]
            setup.connections,  # type: ignore[arg-type]
            settings=pipeline_with()[0]._settings,
        )
        response = setup.ask(client, connection_id=str(local.id))

    assert response.status_code == 422  # type: ignore[attr-defined]
    assert response.json()["error"] == "connection_unsafe"  # type: ignore[attr-defined]
    assert setup.audit.lines[-1].outcome == "connection_unsafe"


def test_testing_a_database_again_drops_its_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection_routes, "_check", lambda *_args, **_kwargs: None)
    setup = Setup()
    with TestClient(setup.app) as client:
        setup.ask(client, connection_id=str(setup.sales.id))
        assert client.post(f"/connections/{setup.sales.id}/test").status_code == 200

    assert setup.sales.id not in setup.registry._cache  # the next question reads its tables again
