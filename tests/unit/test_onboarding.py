"""Onboarding a saved database (5.10): the job, the dictionary as a layer, edits and the routes.

No database, no real LLM: the schema read and the connection are replaced, the LLM is a fake.
"""

from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.agent.pipeline import Pipeline
from querymind.agent.schemas import AnswerSource, AskResult
from querymind.api import connections as connection_routes
from querymind.api.main import create_app
from querymind.api.onboarding import DictionaryEdit, TableEdit, apply_edit
from querymind.db.errors import RemoteUnreachableError
from querymind.db.remote import ConnectionSpec
from querymind.db.schema import Column, SchemaInfo, Table
from querymind.llm import LLMAuthError, LLMOutputError, LLMProviderError, LLMRateLimitError
from querymind.llm.fake import FakeLLMProvider
from querymind.llm.workspace import WorkspaceLLMs
from querymind.semantic import onboarding as onboarding_module
from querymind.semantic.dictionary import (
    ColumnDraft,
    DataDictionary,
    DictionaryTable,
    TableDraft,
    layer_from_dictionary,
)
from querymind.semantic.onboarding import NO_KEY, Onboarder, needs_draft
from querymind.store.errors import DictionaryEditError, OnboardingRunningError
from querymind.store.onboarding import OnboardingRecord, OnboardingStatus
from tests.auth_helpers import TEST_USER, signed_in
from tests.unit.test_api import FakePipeline
from tests.unit.test_api_connections import MemoryConnections
from tests.unit.test_conversation import TOP, StubExecutor, pipeline_with
from tests.unit.test_conversation import executor as executor  # the fixture, re-exported
from tests.unit.test_llm_keys import GROQ_KEY, MemoryAudit, MemoryKeys, build

SCHEMA = SchemaInfo(
    tables=(
        Table(name="customers", columns=(Column(name="company_name", data_type="text"),)),
        Table(name="orders", columns=(Column(name="order_date", data_type="date"),)),
        Table(name="Weird Table", columns=(Column(name="x", data_type="int"),)),
    )
)
SPEC = ConnectionSpec(host="db.example.com", dbname="sales", user="reader")


def draft(text: str, column: str) -> TableDraft:
    return TableDraft(description=text, columns=[ColumnDraft(name=column, description="a value")])


CUSTOMERS = draft("One row per customer company.", "company_name")
ORDERS = draft("One row per order.", "order_date")


# --- The dictionary as a semantic layer -----------------------------------------------------------


def test_the_layer_keeps_only_what_exists_now_and_reports_odd_names() -> None:
    dictionary = DataDictionary(
        database="Sales",
        tables={
            "customers": DictionaryTable(
                description="Client companies.",
                columns={"company_name": "Name", "dropped_column": "Gone"},
            ),
            "old_table": DictionaryTable(description="Dropped last week."),
            "Weird Table": DictionaryTable(description="Has a space."),
            "orders": DictionaryTable(description="", columns={"order_date": ""}),
        },
    )

    layer, skipped = layer_from_dictionary("Sales", "The sales DB.", dictionary, SCHEMA)

    assert set(layer.tables) == {"customers"}  # old_table gone, orders has nothing to say
    assert set(layer.tables["customers"].columns) == {"company_name"}
    assert skipped == ["Weird Table"]
    assert layer.database == "Sales"


def test_a_table_needs_a_draft_until_described_or_reviewed() -> None:
    table = SCHEMA.tables[0]
    assert needs_draft(table, None)
    assert needs_draft(table, DataDictionary(database="x", tables={"customers": DictionaryTable()}))
    described = DictionaryTable(description="Clients.")
    assert not needs_draft(table, DataDictionary(database="x", tables={"customers": described}))
    reviewed = DictionaryTable(reviewed=True)
    assert not needs_draft(table, DataDictionary(database="x", tables={"customers": reviewed}))


# --- The job --------------------------------------------------------------------------------------


class MemoryOnboarding:
    """Stands in for OnboardingStore."""

    def __init__(self) -> None:
        self.status: OnboardingStatus | None = None
        self.dictionary: DataDictionary | None = None
        self.version = 0
        self.error: str | None = None
        self.progress_seen: list[tuple[str, int | None]] = []
        self.skipped = 0
        self.gone = False

    def queue(self, connection_id: UUID) -> OnboardingRecord:
        if self.status in ("queued", "reading", "drafting"):
            raise OnboardingRunningError("running")
        self.status = "queued"
        return self.record(connection_id)

    def progress(self, connection_id: UUID, *, status: OnboardingStatus, **counts: Any) -> bool:
        if self.gone:
            return False
        self.status = status
        self.progress_seen.append((status, counts.get("drafted")))
        return True

    def stored_dictionary(self, connection_id: UUID) -> DataDictionary | None:
        return self.dictionary

    def dictionary_for(self, connection_id: UUID) -> tuple[DataDictionary, int] | None:
        return (self.dictionary, self.version) if self.dictionary and self.version else None

    def save_drafts(self, connection_id: UUID, merge: Any) -> bool:
        self.dictionary = merge(self.dictionary)
        return True

    def finish(
        self, connection_id: UUID, merge: Any, *, generated_by: str, skipped: int, drafted: int = 0
    ) -> bool:
        self.dictionary = merge(self.dictionary)
        self.version += 1
        self.status = "ready"
        self.skipped = skipped
        return True

    def fail(self, connection_id: UUID, error: str) -> None:
        self.status = "failed"
        self.error = error

    def record(self, connection_id: UUID) -> OnboardingRecord:
        now = datetime.now(UTC)
        return OnboardingRecord(
            connection_id=connection_id,
            status=self.status or "queued",
            tables_total=0,
            tables_drafted=0,
            tables_skipped=self.skipped,
            error=self.error,
            generated_by="",
            dictionary=self.dictionary or DataDictionary(database=""),
            dictionary_version=self.version,
            started_at=now,
            updated_at=now,
        )

    def get(self, workspace_id: UUID, connection_id: UUID) -> OnboardingRecord | None:
        return self.record(connection_id) if self.status else None

    def edit(
        self, workspace_id: UUID, connection_id: UUID, dictionary: DataDictionary
    ) -> OnboardingRecord:
        if self.status in ("queued", "reading", "drafting"):
            raise OnboardingRunningError("This database is being onboarded right now.")
        self.dictionary = dictionary
        self.version += 1
        return self.record(connection_id)


class FakeLLMs:
    def __init__(self, llm: FakeLLMProvider | None) -> None:
        self.llm = llm

    def provider_for(self, workspace_id: UUID) -> FakeLLMProvider | None:
        return self.llm


class Job:
    """An onboarder over fakes; `run` happens in the test's thread."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *answers: object, key: bool = True) -> None:
        monkeypatch.setattr(onboarding_module, "connect_remote", lambda *a, **k: nullcontext())
        monkeypatch.setattr(onboarding_module, "read_schema", lambda conn: SCHEMA)
        self.store = MemoryOnboarding()
        self.connections = MemoryConnections()
        self.record = self.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
        self.llm = FakeLLMProvider(answers) if key else None  # type: ignore[arg-type]
        self.waits: list[float] = []
        self.ready: list[UUID] = []
        self.onboarder = Onboarder(
            self.store,  # type: ignore[arg-type]
            self.connections,  # type: ignore[arg-type]
            FakeLLMs(self.llm),  # type: ignore[arg-type]
            describe_error=lambda exc: f"failed: {type(exc).__name__}",
            on_ready=self.ready.append,
            sleep=self.waits.append,
        )

    def run(self) -> None:
        self.store.status = "queued"
        self.onboarder.run(TEST_USER.workspace_id, self.record.id)


def test_a_run_drafts_every_plain_table_and_becomes_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, CUSTOMERS, ORDERS)

    job.run()

    assert job.store.status == "ready" and job.store.version == 1
    assert job.store.dictionary is not None
    assert job.store.dictionary.tables["customers"].description == "One row per customer company."
    assert set(job.store.dictionary.tables) == {"customers", "orders"}
    assert job.store.skipped == 1  # "Weird Table": not a plain lowercase name
    assert job.ready == [job.record.id]  # the database's pipeline is rebuilt


def test_no_own_key_fails_the_run_with_how_to_fix_it(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, key=False)

    job.run()

    assert (job.store.status, job.store.error) == ("failed", NO_KEY)


def test_a_rate_limit_is_waited_out_then_the_run_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    limited = LLMRateLimitError("429", retry_after_seconds=7)
    job = Job(monkeypatch, limited, CUSTOMERS, ORDERS)

    job.run()

    assert job.waits == [7]
    assert job.store.status == "ready"


def test_an_overloaded_provider_is_waited_out_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """Seen live with Gemini: "503 UNAVAILABLE, high demand" in the middle of a run."""
    job = Job(monkeypatch, CUSTOMERS, LLMProviderError("503 high demand"), ORDERS)

    job.run()

    assert job.waits == [5.0]
    assert job.store.status == "ready"


def test_a_failed_run_keeps_what_it_drafted(monkeypatch: pytest.MonkeyPatch) -> None:
    down = LLMProviderError("503")
    job = Job(monkeypatch, CUSTOMERS, *[down] * 5)  # orders fails after every wait

    job.run()

    assert job.store.status == "failed"
    assert job.waits == [5.0, 10.0, 20.0, 40.0]
    assert job.store.dictionary is not None and set(job.store.dictionary.tables) == {"customers"}


def test_a_refused_key_stops_the_run_with_a_safe_message(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, LLMAuthError(f"401 {GROQ_KEY}"))

    job.run()

    assert job.store.status == "failed"
    assert job.store.error == "failed: LLMAuthError"
    assert GROQ_KEY not in (job.store.error or "")


def test_an_answer_that_doesnt_fit_skips_that_table_only(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, LLMOutputError("not a TableDraft"), ORDERS)

    job.run()

    assert job.store.status == "ready"
    assert job.store.dictionary is not None and set(job.store.dictionary.tables) == {"orders"}
    assert job.store.skipped == 2  # the odd name, and the table whose draft failed


def test_a_new_run_drafts_only_what_is_still_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, ORDERS)  # one answer queued: asking for customers would fail
    job.store.dictionary = DataDictionary(
        database="Sales",
        tables={"customers": DictionaryTable(reviewed=True, description="Checked by a person.")},
    )

    job.run()

    assert job.store.dictionary is not None
    assert job.store.dictionary.tables["customers"].description == "Checked by a person."
    assert len(job.llm.calls) == 1  # type: ignore[union-attr]


def test_no_failure_ever_leaves_a_run_running(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch)

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise RemoteUnreachableError("Couldn't reach the database.")

    monkeypatch.setattr(onboarding_module, "connect_remote", unreachable)

    job.run()

    assert (job.store.status, job.store.error) == ("failed", "failed: RemoteUnreachableError")


def test_a_database_deleted_mid_run_stops_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, CUSTOMERS, ORDERS)
    job.store.gone = True

    job.run()  # no error

    assert job.store.version == 0 and job.ready == []


def test_the_table_cap_counts_what_it_leaves_out(monkeypatch: pytest.MonkeyPatch) -> None:
    job = Job(monkeypatch, CUSTOMERS)
    job.onboarder._max_tables = 1

    job.run()

    assert job.store.dictionary is not None and set(job.store.dictionary.tables) == {"customers"}
    assert job.store.skipped == 2  # orders over the cap, the odd name


# --- Edits ----------------------------------------------------------------------------------------


STORED = DataDictionary(
    database="Sales",
    tables={
        "customers": DictionaryTable(description="Draft.", columns={"company_name": "Name"}),
        "orders": DictionaryTable(description="Orders.", columns={"order_date": "When"}),
    },
)


def test_an_edit_changes_only_what_it_names() -> None:
    edit = DictionaryEdit(
        tables={"customers": TableEdit(description=" Client companies. ", reviewed=True)}
    )

    edited = apply_edit(STORED, edit)

    assert edited.tables["customers"] == DictionaryTable(
        reviewed=True, description="Client companies.", columns={"company_name": "Name"}
    )
    assert edited.tables["orders"] == STORED.tables["orders"]


@pytest.mark.parametrize(
    "edit",
    [
        DictionaryEdit(tables={"orderz": TableEdit(description="typo")}),
        DictionaryEdit(tables={"orders": TableEdit(columns={"secret_column": "new"})}),
    ],
)
def test_an_edit_cant_add_tables_or_columns(edit: DictionaryEdit) -> None:
    with pytest.raises(DictionaryEditError):
        apply_edit(STORED, edit)


# --- The routes -----------------------------------------------------------------------------------


class Routes:
    def __init__(self, *, key: bool = True) -> None:
        self.connections = MemoryConnections()
        self.store = MemoryOnboarding()
        self.keys = MemoryKeys()
        if key:
            self.keys.save(
                TEST_USER.workspace_id, "groq", "openai/gpt-oss-120b", SecretStr(GROQ_KEY)
            )
        self.started: list[UUID] = []
        self.audit = MemoryAudit()
        outer = self

        class Starter:
            def start(self, workspace_id: UUID, connection_id: UUID) -> OnboardingRecord:
                outer.started.append(connection_id)
                return outer.store.queue(connection_id)

        self.app = signed_in(
            create_app(
                pipeline=FakePipeline(),
                connections=self.connections,  # type: ignore[arg-type]
                onboarding=self.store,  # type: ignore[arg-type]
                onboarder=Starter(),  # type: ignore[arg-type]
                audit=self.audit,  # type: ignore[arg-type]
                workspace_llms=WorkspaceLLMs(self.keys, self.keys, build=build),  # type: ignore[arg-type]
            )
        )


def test_saving_a_database_onboards_it_when_there_is_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(connection_routes, "_check", lambda *a, **k: None)
    with_key, without_key = Routes(), Routes(key=False)
    body = {
        "name": "Sales",
        "host": "db.example.com",
        "dbname": "sales",
        "user": "r",
        "password": "p",
    }
    with TestClient(with_key.app) as client:
        saved = client.post("/connections", json=body).json()
    with TestClient(without_key.app) as client:
        assert client.post("/connections", json=body).status_code == 201  # saved all the same

    assert with_key.started == [UUID(saved["id"])]
    assert "onboarding_started" in [line.action for line in with_key.audit.lines]
    assert without_key.started == []


def test_onboard_needs_a_key_and_refuses_a_second_run() -> None:
    routes = Routes()
    record = routes.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
    with TestClient(routes.app) as client:
        first = client.post(f"/connections/{record.id}/onboard")
        second = client.post(f"/connections/{record.id}/onboard")
        status = client.get(f"/connections/{record.id}/onboarding")

    assert first.status_code == 202 and first.json()["status"] == "queued"
    assert second.status_code == 409 and second.json()["error"] == "onboarding_running"
    assert status.json()["status"] == "queued"

    no_key = Routes(key=False)
    record = no_key.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
    with TestClient(no_key.app) as client:
        refused = client.post(f"/connections/{record.id}/onboard")
    assert refused.status_code == 409 and refused.json()["error"] == "llm_key_required"


def test_the_dictionary_is_read_edited_and_never_during_a_run() -> None:
    routes = Routes()
    record = routes.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
    routes.store.status, routes.store.dictionary, routes.store.version = "ready", STORED, 1
    edit = {"tables": {"customers": {"description": "Client companies.", "reviewed": True}}}
    with TestClient(routes.app) as client:
        assert client.get(f"/connections/{record.id}/dictionary").json()["tables"]["orders"]
        edited = client.put(f"/connections/{record.id}/dictionary", json=edit)
        bad = client.put(
            f"/connections/{record.id}/dictionary",
            json={"tables": {"orderz": {"description": "x"}}},
        )
        routes.store.status = "drafting"
        during_run = client.put(f"/connections/{record.id}/dictionary", json=edit)

    assert edited.status_code == 200
    assert edited.json()["tables"]["customers"]["reviewed"] is True
    assert bad.status_code == 422 and bad.json()["error"] == "dictionary_invalid"
    assert during_run.status_code == 409 and during_run.json()["error"] == "onboarding_running"
    assert "dictionary_edited" in [line.action for line in routes.audit.lines]


def test_no_dictionary_yet_is_a_404() -> None:
    routes = Routes()
    record = routes.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("p"))
    with TestClient(routes.app) as client:
        assert client.get(f"/connections/{record.id}/onboarding").status_code == 404
        assert client.get(f"/connections/{record.id}/dictionary").status_code == 404


# --- The pipeline uses it -------------------------------------------------------------------------


def test_the_drafted_descriptions_reach_the_prompt_and_name_the_answer(
    executor: StubExecutor,
) -> None:
    """test_conversation's schema: customers and orders. The dictionary also names a table that
    no longer exists: it is left out, and the question is answered all the same."""
    settings = pipeline_with()[0]._settings
    dictionary = DataDictionary(
        database="Sales",
        tables={
            "customers": DictionaryTable(
                description="Client companies of the food business.",
                columns={"company_name": "The client's registered name"},
            ),
            "dropped_last_week": DictionaryTable(description="Gone."),
        },
    )
    pipeline = Pipeline(
        llm=FakeLLMProvider(),
        settings=settings.model_copy(update={"semantic_layer_paths": []}),
        connect=MagicMock(),
        source=AnswerSource(database="Sales", description="Your database sales."),
        dictionary=dictionary,
    )
    llm = FakeLLMProvider([TOP])

    reply = pipeline.ask("Top customers?", llm=llm, summarize_answer=False)

    assert isinstance(reply, AskResult)
    prompt = llm.calls[0].prompt
    assert "Client companies of the food business" in prompt
    assert "The client's registered name" in prompt
    assert "dropped_last_week" not in prompt
    assert [source.database for source in reply.sources] == ["Sales"]
