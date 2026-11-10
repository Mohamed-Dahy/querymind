"""Done when (5.11): two saved databases answer side by side without seeing each other's tables.

Two throwaway logins on the Docker database stand in for two users' databases: A can read only
`customers`, B also `orders`. Each gets its own pipeline through the registry (connect_remote,
development mode: Docker is on 127.0.0.1 without TLS). The LLM is a fake passed with each
question, as a workspace's own key would be.
"""

from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from querymind.agent.registry import PipelineRegistry
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.config import Settings
from querymind.db.errors import NoReadableTablesError
from querymind.db.remote import ConnectionSpec
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.errors import UnsafeSqlError
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.store import LocalSecretStore
from querymind.store.connections import ConnectionRecord, ConnectionStore
from querymind.store.conversations import ConversationStore, Owner
from querymind.store.errors import ConversationNotFoundError, ConversationOtherDatabaseError
from tests.integration.test_remote_databases import PASSWORD, MakeRole
from tests.integration.test_remote_databases import admin as admin  # fixtures, re-exported
from tests.integration.test_remote_databases import make_role as make_role

pytestmark = pytest.mark.integration


def draft(sql: str) -> SqlDraft:
    return SqlDraft(sql=sql, explanation="Test.", tables_used=[])


@pytest.fixture
def connections(settings: Settings) -> ConnectionStore:
    key = SecretStr(Fernet.generate_key().decode())
    return ConnectionStore(settings, LocalSecretStore(settings, cipher=SecretCipher([key])))


@pytest.fixture
def registry(settings: Settings, connections: ConnectionStore) -> PipelineRegistry:
    return PipelineRegistry(
        demo=None,  # type: ignore[arg-type]  # not asked here
        connections=connections,
        settings=settings,
        allow_private=True,
    )


def saved(
    connections: ConnectionStore, owner: Owner, make_role: MakeRole, name: str, *grants: str
) -> ConnectionRecord:
    return connections.save(owner.workspace_id, name, make_role(*grants), PASSWORD)


def test_two_databases_answer_side_by_side_each_with_only_its_tables(
    registry: PipelineRegistry,
    connections: ConnectionStore,
    owner: Owner,
    make_role: MakeRole,
    settings: Settings,
) -> None:
    a = saved(connections, owner, make_role, "A")
    b = saved(connections, owner, make_role, "B", "GRANT SELECT ON public.orders TO {role}")
    pipeline_a = registry.for_database(owner.workspace_id, a.id)
    pipeline_b = registry.for_database(owner.workspace_id, b.id)
    assert pipeline_a is not pipeline_b

    # A answers from its own table, says which database answered, and was shown only its table.
    llm_a = FakeLLMProvider([draft("SELECT count(*) AS n FROM customers")])
    reply = pipeline_a.ask("How many customers?", llm=llm_a, summarize_answer=False)
    assert isinstance(reply, AskResult) and reply.result.rows == [[91]]
    assert [source.database for source in reply.sources] == ["A"]
    prompt = llm_a.calls[0].prompt
    assert "customers" in prompt and "orders" not in prompt

    # A can't read B's table, whatever the LLM writes: the validator refuses it every time.
    attempts = settings.max_sql_attempts
    llm_a = FakeLLMProvider([draft("SELECT count(*) FROM orders")] * attempts)
    with pytest.raises(UnsafeSqlError):
        pipeline_a.ask("How many orders?", llm=llm_a, summarize_answer=False)

    # B, side by side, can.
    llm_b = FakeLLMProvider([draft("SELECT count(*) AS n FROM orders")])
    reply = pipeline_b.ask("How many orders?", llm=llm_b, summarize_answer=False)
    assert isinstance(reply, AskResult) and reply.result.rows == [[830]]
    assert [source.database for source in reply.sources] == ["B"]


def test_a_login_that_can_read_nothing_is_told_so(
    registry: PipelineRegistry, connections: ConnectionStore, owner: Owner, make_role: MakeRole
) -> None:
    empty = saved(
        connections, owner, make_role, "Empty", "REVOKE SELECT ON public.customers FROM {role}"
    )
    pipeline = registry.for_database(owner.workspace_id, empty.id)

    with pytest.raises(NoReadableTablesError, match="no tables in schema public"):
        pipeline.ask("Anything?", llm=FakeLLMProvider(), summarize_answer=False)


@pytest.fixture
def conversations(settings: Settings) -> ConversationStore:
    return ConversationStore(settings)


def test_deleting_a_database_deletes_its_conversations(
    connections: ConnectionStore, conversations: ConversationStore, owner: Owner
) -> None:
    record = connections.save(
        owner.workspace_id,
        "Sales",
        ConnectionSpec(host="db.example.com", dbname="sales", user="reader"),
        PASSWORD,
    )
    conversation = uuid4()
    conversations.save_turn(
        conversation,
        owner=owner,
        question="Top customers?",
        kind="answer",
        sql="SELECT 1",
        reply={"kind": "answer"},
        connection_id=record.id,
    )
    assert conversations.database_of(conversation, owner=owner) == (True, record.id)

    connections.delete(owner.workspace_id, record.id)

    with pytest.raises(ConversationNotFoundError):
        conversations.get(conversation, owner=owner)


def test_a_conversation_keeps_its_database(
    connections: ConnectionStore, conversations: ConversationStore, owner: Owner
) -> None:
    """Two first turns racing with different databases: the second can't join the first's."""
    record = connections.save(
        owner.workspace_id,
        "Sales",
        ConnectionSpec(host="db.example.com", dbname="sales", user="reader"),
        PASSWORD,
    )
    conversation = uuid4()
    turn = {"question": "Q?", "kind": "answer", "sql": "SELECT 1", "reply": {"kind": "answer"}}
    conversations.save_turn(conversation, owner=owner, connection_id=record.id, **turn)  # type: ignore[arg-type]

    with pytest.raises(ConversationOtherDatabaseError):
        conversations.save_turn(conversation, owner=owner, connection_id=None, **turn)  # type: ignore[arg-type]
    assert conversations.save_turn(conversation, owner=owner, connection_id=record.id, **turn) == 2  # type: ignore[arg-type]
