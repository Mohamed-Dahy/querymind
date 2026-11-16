"""The explorer on a workspace's own database (U7.1b): `?connection_id=` on /catalog, /semantic and
the table questions. Fakes for the database and the store; no DB, no LLM, no key."""

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from querymind.agent.registry import PipelineRegistry
from querymind.api.main import create_app
from querymind.db.errors import RemoteUnreachableError
from querymind.db.schema import Column, SchemaInfo, Table
from querymind.semantic.dictionary import DataDictionary, DictionaryTable, layer_from_dictionary
from querymind.semantic.models import SemanticLayer
from querymind.store.connections import ConnectionRecord
from querymind.store.conversations import Owner, TableQuestion
from tests.auth_helpers import OTHER_USER, TEST_USER, signed_in
from tests.unit.test_api_connections import MemoryConnections
from tests.unit.test_conversation import pipeline_with
from tests.unit.test_registry import SPEC

SECRET = "Ana Trujillo"
SCHEMA = SchemaInfo(
    tables=(
        Table(
            name="clients",
            columns=(Column(name="name", data_type="text"), Column(name="city", data_type="text")),
            sample_rows=((SECRET, "Berlin"),),
        ),
        Table(name="invoices", columns=(Column(name="total", data_type="numeric"),)),
    )
)
DICTIONARY = DataDictionary(
    database="Sales",
    tables={
        "clients": DictionaryTable(
            description="Who buys from us.",
            columns={"name": "The client's name.", "city": "Where they are."},
            pii=["name"],
        )
    },
)


class FakeSavedPipeline:
    """A saved database's pipeline, as the explorer uses it. Counts reads; can fail."""

    def __init__(self, dictionary: DataDictionary | None, error: Exception | None = None) -> None:
        self.dictionary = dictionary
        self.error = error
        self.reads = 0
        self.count_missing: list[bool] = []

    def described_schema(self) -> tuple[SchemaInfo, list[SemanticLayer], DataDictionary | None]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        if self.dictionary is None:
            return SCHEMA, [], None
        layer, _ = layer_from_dictionary("Sales", "Your database sales.", self.dictionary, SCHEMA)
        return SCHEMA, [layer], self.dictionary

    def row_counts(self, *, count_missing: bool) -> dict[str, int | None]:
        self.count_missing.append(count_missing)
        return {"clients": 42, "invoices": None}  # never analysed: not known


class FakeStore:
    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []

    def recent_questions_using(self, table: str, **kw: Any) -> list[TableQuestion]:
        self.asked.append({"table": table, **kw})
        return []


class FakeAccess:
    """Analysts may use only `clients` on the saved database; the demo is open."""

    def __init__(self, sales: UUID) -> None:
        self.sales = sales
        self.asked: list[tuple[UUID | None, str]] = []

    def allowed(self, workspace_id: UUID, connection_id: UUID | None, role: str) -> Any:
        self.asked.append((connection_id, role))
        if role == "admin" or connection_id != self.sales:
            return None
        return frozenset({"clients"})


class Setup:
    def __init__(
        self, *, dictionary: DataDictionary | None = DICTIONARY, error: Exception | None = None
    ) -> None:
        self.connections = MemoryConnections()
        self.sales = self.connections.save(TEST_USER.workspace_id, "Sales", SPEC, SecretStr("pw"))
        self.theirs = self.connections.save(OTHER_USER.workspace_id, "Theirs", SPEC, SecretStr("p"))
        self.pipeline = FakeSavedPipeline(dictionary, error)
        self.store = FakeStore()
        self.access = FakeAccess(self.sales.id)
        registry = PipelineRegistry(
            object(),  # type: ignore[arg-type]  # the demo: not used here
            self.connections,  # type: ignore[arg-type]
            settings=pipeline_with()[0]._settings,
            build=self._build,
        )
        self.registry = registry

    def _build(self, record: ConnectionRecord, password: SecretStr, dictionary: object) -> Any:
        return self.pipeline

    def client(self, user: Any = TEST_USER) -> TestClient:
        app = create_app(
            pipeline=object(),  # type: ignore[arg-type]
            store=self.store,  # type: ignore[arg-type]
            connections=self.connections,  # type: ignore[arg-type]
            pipelines=self.registry,
            table_access=self.access,  # type: ignore[arg-type]
        )
        return TestClient(signed_in(app, user), raise_server_exceptions=False)


def test_the_catalog_of_a_saved_database_comes_from_its_own_schema() -> None:
    setup = Setup()
    with setup.client() as api:
        body = api.get("/catalog", params={"connection_id": str(setup.sales.id)}).json()

    tables = {table["name"]: table for table in body["tables"]}
    assert set(tables) == {"clients", "invoices"}
    assert tables["clients"]["business"] == "Sales" and tables["clients"]["row_count"] == 42
    assert tables["invoices"]["row_count"] is None  # no estimate, and no count(*) on their server
    assert setup.pipeline.count_missing == [False]


def test_its_meanings_come_from_its_dictionary_with_personal_data_flagged() -> None:
    setup = Setup()
    with setup.client() as api:
        body = api.get("/semantic", params={"connection_id": str(setup.sales.id)}).json()

    [business] = body["businesses"]
    assert business["database"] == "Sales"
    clients = next(table for table in business["tables"] if table["name"] == "clients")
    assert clients["description"] == "Who buys from us."
    flags = {column["name"]: column["pii"] for column in clients["columns"]}
    assert flags == {"name": True, "city": False}


def test_no_value_from_the_database_reaches_the_client() -> None:
    setup = Setup()
    with setup.client() as api:
        catalog = api.get("/catalog", params={"connection_id": str(setup.sales.id)})
        semantic = api.get("/semantic", params={"connection_id": str(setup.sales.id)})

    assert catalog.status_code == semantic.status_code == 200
    assert SECRET not in catalog.text and SECRET not in semantic.text


def test_not_onboarded_yet_the_tables_show_but_no_business() -> None:
    setup = Setup(dictionary=None)
    with setup.client() as api:
        catalog = api.get("/catalog", params={"connection_id": str(setup.sales.id)}).json()
        semantic = api.get("/semantic", params={"connection_id": str(setup.sales.id)}).json()

    assert [table["business"] for table in catalog["tables"]] == [None, None]
    assert semantic == {"businesses": []}


def test_the_schema_is_read_once_and_kept_with_its_pipeline() -> None:
    setup = Setup()
    with setup.client() as api:
        for _ in range(3):
            api.get("/catalog", params={"connection_id": str(setup.sales.id)})
        api.get("/semantic", params={"connection_id": str(setup.sales.id)})
        assert setup.pipeline.reads == 1

        # "Test again" or a delete drops the pipeline: the next look reads the schema again.
        setup.pipeline = FakeSavedPipeline(DICTIONARY)
        setup.registry.forget(setup.sales.id)
        api.get("/catalog", params={"connection_id": str(setup.sales.id)})
    assert setup.pipeline.reads == 1


@pytest.mark.parametrize("route", ["/catalog", "/semantic", "/catalog/tables/clients/questions"])
def test_another_workspaces_database_is_the_same_404_as_none(route: str) -> None:
    setup = Setup()
    with setup.client() as api:
        theirs = api.get(route, params={"connection_id": str(setup.theirs.id)})
        missing = api.get(route, params={"connection_id": str(uuid4())})

    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()
    assert theirs.json()["error"] == "connection_not_found"
    assert setup.pipeline.reads == 0


def test_a_role_sees_only_its_tables_on_that_database() -> None:
    setup = Setup()
    analyst = TEST_USER.model_copy(update={"role": "analyst"})
    with setup.client(analyst) as api:
        catalog = api.get("/catalog", params={"connection_id": str(setup.sales.id)}).json()
        semantic = api.get("/semantic", params={"connection_id": str(setup.sales.id)}).json()
        hidden = api.get(
            "/catalog/tables/invoices/questions", params={"connection_id": str(setup.sales.id)}
        ).json()

    assert [table["name"] for table in catalog["tables"]] == ["clients"]
    assert [t["name"] for t in semantic["businesses"][0]["tables"]] == ["clients"]
    assert hidden == {"questions": []}
    # Asked about this database, never the demo's rules.
    assert set(setup.access.asked) == {(setup.sales.id, "analyst")}


def test_table_questions_are_this_databases_only() -> None:
    setup = Setup()
    with setup.client() as api:
        api.get("/catalog/tables/clients/questions", params={"connection_id": str(setup.sales.id)})
        api.get("/catalog/tables/clients/questions")

    assert [asked["connection_id"] for asked in setup.store.asked] == [setup.sales.id, None]
    assert setup.store.asked[0]["owner"] == Owner(
        user_id=TEST_USER.user_id, workspace_id=TEST_USER.workspace_id
    )


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (RemoteUnreachableError("Couldn't reach it."), 422, "connection_unreachable"),
        (psycopg.OperationalError("server closed the connection"), 503, "db_unavailable"),
    ],
)
def test_a_database_that_cant_be_read_is_a_clean_error_and_is_tried_again(
    error: Exception, status: int, code: str
) -> None:
    setup = Setup(error=error)
    with setup.client() as api:
        first = api.get("/catalog", params={"connection_id": str(setup.sales.id)})
        setup.pipeline.error = None
        second = api.get("/catalog", params={"connection_id": str(setup.sales.id)})

    assert (first.status_code, first.json()["error"]) == (status, code)
    assert "server closed" not in first.text
    assert second.status_code == 200  # a failed read isn't kept


def test_without_saved_databases_on_this_server_it_says_so() -> None:
    app = create_app(pipeline=object())  # type: ignore[arg-type]
    with TestClient(signed_in(app), raise_server_exceptions=False) as api:
        response = api.get("/catalog", params={"connection_id": str(uuid4())})

    assert response.status_code == 503


def test_a_bad_id_is_a_clean_422() -> None:
    setup = Setup()
    with setup.client() as api:
        assert api.get("/catalog", params={"connection_id": "not-a-uuid"}).status_code == 422
