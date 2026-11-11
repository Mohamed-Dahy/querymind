"""A saved database's schema index (5.10b): synced at the first question, searched by keyword in
the app database, and never in the way: a small database, or an app database that is down, means
the whole schema (as before)."""

from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

import querymind.agent.pipeline as pipeline_module
import querymind.semantic.retrieval as retrieval_module
from querymind.agent.pipeline import Pipeline
from querymind.agent.registry import NoOwnLLM, PipelineRegistry, database_settings
from querymind.agent.schemas import AnswerSource
from querymind.config import ConfigError, Settings
from querymind.db.connection import connect_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.documents import SchemaDoc
from querymind.semantic.index import IndexHit, SavedDatabaseIndex, SchemaIndexError
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever
from tests.unit.test_api_connections import MemoryConnections
from tests.unit.test_registry import SPEC
from tests.unit.test_retrieval import settings

# A company database bigger than RETRIEVAL_MIN_TABLES (15): 40 tables, a few joined by keys.
# The integration test (tests/integration/test_saved_schema_index.py) searches it for real.
BIG_TABLES = [
    "accounts",
    "invoices",
    "invoice_lines",
    "payments",
    "customers",
    "suppliers",
    "purchase_orders",
    "purchase_order_lines",
    "products",
    "product_categories",
    "warehouses",
    "stock_levels",
    "shipments",
    "carriers",
    "employees",
    "departments",
    "salaries",
    "timesheets",
    "projects",
    "project_tasks",
    "expenses",
    "budgets",
    "ledger_entries",
    "tax_rates",
    "currencies",
    "exchange_rates",
    "campaigns",
    "leads",
    "opportunities",
    "support_tickets",
    "ticket_comments",
    "contracts",
    "assets",
    "maintenance_logs",
    "vehicles",
    "fuel_logs",
    "trainings",
    "certifications",
    "audit_events",
    "app_settings",
]
KEYS = {  # table → (its column, the table it points to)
    "invoices": ("customer_id", "customers"),
    "invoice_lines": ("invoice_id", "invoices"),
    "shipments": ("carrier_id", "carriers"),
    "salaries": ("employee_id", "employees"),
    "employees": ("department_id", "departments"),
    "ticket_comments": ("ticket_id", "support_tickets"),
}


def big_table(name: str) -> Table:
    columns = [Column(name="id", data_type="integer"), Column(name="status", data_type="text")]
    keys: tuple[ForeignKey, ...] = ()
    if name in KEYS:
        column, target = KEYS[name]
        columns.append(Column(name=column, data_type="integer"))
        keys = (ForeignKey(columns=(column,), ref_table=target, ref_columns=("id",)),)
    return Table(name=name, columns=tuple(columns), primary_key=("id",), foreign_keys=keys)


BIG_SCHEMA = SchemaInfo(tables=tuple(big_table(name) for name in BIG_TABLES))
SMALL_SCHEMA = SchemaInfo(tables=tuple(big_table(name) for name in BIG_TABLES[:5]))
DICTIONARY = DataDictionary(
    database="Books",
    tables={"invoices": DictionaryTable(description="Bills sent to clients, one row per bill.")},
)


class Sync:
    """Stands in for sync_connection_documents: records what would be written."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[UUID, list[SchemaDoc]]] = []

    def __call__(self, conn: object, connection_id: UUID, docs: list[SchemaDoc]) -> tuple[int, int]:
        if self.error is not None:
            raise self.error
        self.calls.append((connection_id, list(docs)))
        return len(docs), 0


def saved_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    schema: SchemaInfo = BIG_SCHEMA,
    sync: Sync | None = None,
    **overrides: Any,
) -> tuple[Pipeline, UUID, Sync]:
    monkeypatch.setattr(pipeline_module, "read_schema", lambda conn: schema)
    sync = sync or Sync()
    monkeypatch.setattr(pipeline_module, "sync_connection_documents", sync)
    connection_id = uuid4()
    pipeline = Pipeline(
        llm=NoOwnLLM(),
        settings=database_settings(settings(**overrides)),
        connect=MagicMock(),  # the user's database: only read_schema (patched) uses it here
        source=AnswerSource(database="Books", description="Your database books."),
        dictionary=DICTIONARY,
        saved_index=SavedDatabaseIndex(connection_id, MagicMock()),
    )
    return pipeline, connection_id, sync


def test_a_big_saved_database_is_indexed_at_its_first_question_and_searched_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, connection_id, sync = saved_pipeline(monkeypatch)
    searched: list[UUID | None] = []

    def fake_keyword_search(conn: object, question: str, **kw: Any) -> list[IndexHit]:
        searched.append(kw["connection_id"])
        return [IndexHit(id="table:invoices", kind="table", table="invoices", score=0.9)]

    monkeypatch.setattr(retrieval_module, "keyword_search", fake_keyword_search)

    _, retriever = pipeline._schema_info()
    assert retriever is not None
    selection = retriever.select("How many bills did we send?")

    (synced_id, docs) = sync.calls[0]
    assert synced_id == connection_id and len(docs) == 40
    invoices = next(doc for doc in docs if doc.id == "table:invoices")
    assert "Bills sent to clients" in invoices.text  # the dictionary is in what is searched
    assert searched == [connection_id]
    assert selection.info.mode == "retrieved" and selection.info.search == "keyword"
    assert selection.info.tables == ("invoices",)  # the one table it needs, not all 40
    pipeline._schema_info()
    assert len(sync.calls) == 1  # once per pipeline: the schema is read once


def test_a_small_saved_database_sends_its_whole_schema_and_indexes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline, _, sync = saved_pipeline(monkeypatch, schema=SMALL_SCHEMA)

    _, retriever = pipeline._schema_info()

    assert retriever is None and sync.calls == []


def test_when_the_app_database_is_down_the_whole_schema_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    down = Sync(error=DatabaseUnavailableError("Could not connect to the app's database."))
    pipeline, _, _ = saved_pipeline(monkeypatch, sync=down)

    _, retriever = pipeline._schema_info()

    assert retriever is None


def test_with_retrieval_on_a_failed_sync_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    down = Sync(error=DatabaseUnavailableError("down"))
    pipeline, _, _ = saved_pipeline(monkeypatch, sync=down)
    pipeline._settings = pipeline._settings.model_copy(update={"schema_retrieval": "on"})

    with pytest.raises(SchemaIndexError):
        pipeline._schema_info()


def test_a_saved_database_index_is_searched_by_keyword_only() -> None:
    with pytest.raises(ConfigError, match="keyword"):
        Pipeline(
            llm=NoOwnLLM(),
            settings=settings(retrieval_mode="hybrid"),
            saved_index=SavedDatabaseIndex(uuid4(), MagicMock()),
        )


def test_only_keyword_search_runs_without_embeddings() -> None:
    catalog = SchemaCatalog(SMALL_SCHEMA, [])

    with pytest.raises(ValueError, match="embeddings"):
        SchemaRetriever(
            catalog=catalog,
            embeddings=None,
            connect=MagicMock(),
            settings=settings(retrieval_mode="hybrid"),
        )
    SchemaRetriever(
        catalog=catalog,
        embeddings=None,
        connect=MagicMock(),
        settings=settings(retrieval_mode="keyword"),
    )


def test_an_app_database_outage_during_a_search_sends_the_whole_schema() -> None:
    def down(_settings: Settings) -> Any:
        raise DatabaseUnavailableError("Could not connect to the app's database.")

    retriever = SchemaRetriever(
        catalog=SchemaCatalog(BIG_SCHEMA, []),
        embeddings=None,
        connect=down,
        settings=settings(retrieval_mode="keyword"),
        connection_id=uuid4(),
    )

    selection = retriever.select("How many invoices?")

    assert selection.info.mode == "fallback" and len(selection.info.tables) == 40


def test_the_registry_gives_each_saved_database_its_index_in_the_app_database() -> None:
    connections = MemoryConnections()
    record = connections.save(uuid4(), "Books", SPEC, SecretStr("pw"))
    registry = PipelineRegistry(
        demo=None,  # type: ignore[arg-type]
        connections=connections,  # type: ignore[arg-type]
        settings=settings(retrieval_mode="hybrid_rerank"),
    )

    pipeline = registry._new_pipeline(record, SecretStr("pw"), None)

    assert pipeline._saved_index == SavedDatabaseIndex(record.id, connect_app)
    assert pipeline._settings.retrieval_mode == "keyword"  # the server's rerank isn't used here
