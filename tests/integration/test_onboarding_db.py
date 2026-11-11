"""Onboarding on the real database (5.10): the store's rules, and the done-when end to end.

Done when: a fresh database answers its first question with no file edited by hand. Here: a
throwaway login on the Docker database is saved, onboarded (a fake LLM drafts the descriptions,
standing in for the workspace's key), and the next question's prompt carries those descriptions.
"""

from uuid import UUID

import psycopg
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from querymind.agent.registry import PipelineRegistry
from querymind.agent.schemas import AskResult, SqlDraft
from querymind.config import Settings
from querymind.db.remote import ConnectionSpec
from querymind.llm.fake import FakeLLMProvider
from querymind.secrets.cipher import SecretCipher
from querymind.secrets.store import LocalSecretStore
from querymind.semantic.dictionary import ColumnDraft, DataDictionary, DictionaryTable, TableDraft
from querymind.semantic.onboarding import Onboarder
from querymind.store.connections import ConnectionStore
from querymind.store.conversations import Owner
from querymind.store.errors import OnboardingRunningError
from querymind.store.onboarding import OnboardingStore
from tests.integration.test_remote_databases import PASSWORD, MakeRole
from tests.integration.test_remote_databases import admin as admin  # fixtures, re-exported
from tests.integration.test_remote_databases import make_role as make_role

pytestmark = pytest.mark.integration

SPEC = ConnectionSpec(host="db.example.com", dbname="sales", user="reader")


@pytest.fixture
def connections(settings: Settings) -> ConnectionStore:
    key = SecretStr(Fernet.generate_key().decode())
    return ConnectionStore(settings, LocalSecretStore(settings, cipher=SecretCipher([key])))


@pytest.fixture
def onboarding(settings: Settings) -> OnboardingStore:
    return OnboardingStore(settings)


def saved_id(connections: ConnectionStore, owner: Owner, spec: ConnectionSpec = SPEC) -> UUID:
    return connections.save(owner.workspace_id, "Sales", spec, PASSWORD).id


def test_one_run_at_a_time_and_progress_doesnt_change_the_version(
    connections: ConnectionStore, onboarding: OnboardingStore, owner: Owner
) -> None:
    connection = saved_id(connections, owner)
    onboarding.queue(connection)

    with pytest.raises(OnboardingRunningError):
        onboarding.queue(connection)
    onboarding.progress(connection, status="drafting", total=3, drafted=1)
    record = onboarding.get(owner.workspace_id, connection)
    assert record is not None
    assert (record.status, record.tables_total, record.tables_drafted) == ("drafting", 3, 1)
    assert record.dictionary_version == 0 and onboarding.dictionary_for(connection) is None


def test_an_edit_waits_for_the_run_and_a_finish_keeps_what_was_reviewed(
    connections: ConnectionStore, onboarding: OnboardingStore, owner: Owner
) -> None:
    connection = saved_id(connections, owner)
    onboarding.queue(connection)
    reviewed = DataDictionary(
        database="Sales",
        tables={"customers": DictionaryTable(reviewed=True, description="Checked.")},
    )
    onboarding.finish(connection, lambda stored: reviewed, generated_by="fake", skipped=0)

    onboarding.queue(connection)
    with pytest.raises(OnboardingRunningError):
        onboarding.edit(owner.workspace_id, connection, reviewed)
    # The run's merge sees the stored dictionary (under a lock), not one it read earlier.
    seen: list[DataDictionary | None] = []

    def merge(stored: DataDictionary | None) -> DataDictionary:
        seen.append(stored)
        return reviewed

    onboarding.finish(connection, merge, generated_by="fake", skipped=0)

    assert seen == [reviewed]
    found = onboarding.dictionary_for(connection)
    assert found is not None and found[1] == 2


def test_a_restart_fails_runs_left_running(
    connections: ConnectionStore, onboarding: OnboardingStore, owner: Owner
) -> None:
    connection = saved_id(connections, owner)
    onboarding.queue(connection)

    assert onboarding.mark_interrupted() >= 1

    record = onboarding.get(owner.workspace_id, connection)
    assert record is not None and record.status == "failed"
    assert "restart" in (record.error or "")


def test_another_workspace_cant_read_it_and_deleting_the_database_removes_it(
    connections: ConnectionStore,
    onboarding: OnboardingStore,
    owner: Owner,
    other_owner: Owner,
    admin: psycopg.Connection,
) -> None:
    connection = saved_id(connections, owner)
    onboarding.queue(connection)

    assert onboarding.get(other_owner.workspace_id, connection) is None
    connections.delete(owner.workspace_id, connection)
    row = admin.execute(
        "SELECT count(*) FROM querymind_app.connection_dictionaries WHERE connection_id = %s",
        (connection,),
    ).fetchone()
    assert row is not None and row[0] == 0


class FakeLLMs:
    def __init__(self, llm: FakeLLMProvider) -> None:
        self.llm = llm

    def provider_for(self, workspace_id: UUID) -> FakeLLMProvider:
        return self.llm


def test_done_when_a_fresh_database_is_onboarded_and_its_next_prompt_knows_its_tables(
    settings: Settings,
    connections: ConnectionStore,
    onboarding: OnboardingStore,
    owner: Owner,
    make_role: MakeRole,
) -> None:
    spec = make_role("GRANT SELECT ON public.orders TO {role}")  # customers + orders
    connection = saved_id(connections, owner, spec)
    drafter = FakeLLMProvider(
        [
            TableDraft(
                description=f"DRAFTED: one row per {name}.",
                columns=[ColumnDraft(name="x", description="unused")],
            )
            for name in ("customers", "orders")
        ]
    )
    registry = PipelineRegistry(
        demo=None,  # type: ignore[arg-type]
        connections=connections,
        settings=settings,
        allow_private=True,
        dictionaries=onboarding,
    )
    onboarder = Onboarder(
        onboarding,
        connections,
        FakeLLMs(drafter),  # type: ignore[arg-type]
        allow_private=True,
        on_ready=registry.forget,
    )

    onboarding.queue(connection)
    onboarder.run(owner.workspace_id, connection)  # what the pool runs, here in this thread

    record = onboarding.get(owner.workspace_id, connection)
    assert record is not None
    assert record.status == "ready", record.error
    assert (record.tables_total, record.tables_drafted) == (2, 2)
    pipeline = registry.for_database(owner.workspace_id, connection)
    asker = FakeLLMProvider(
        [SqlDraft(sql="SELECT count(*) AS n FROM customers", explanation="Count.", tables_used=[])]
    )
    reply = pipeline.ask("How many customers?", llm=asker, summarize_answer=False)

    assert isinstance(reply, AskResult) and reply.result.rows == [[91]]
    assert "DRAFTED: one row per" in asker.calls[0].prompt
    assert [source.database for source in reply.sources] == ["Sales"]
    onboarder.shutdown()
