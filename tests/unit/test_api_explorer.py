"""The explorer endpoints over the real YAML files, with the database replaced. No DB, no LLM."""

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from querymind.api.explorer import ExplorerData
from querymind.api.main import create_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import Column, SchemaInfo, Table
from querymind.store.conversations import TableQuestion
from querymind.store.errors import StoreUnavailableError

LAYERS = [Path("semantic_layer/northwind.yaml"), Path("semantic_layer/chinook.yaml")]
SECRET = "Maria Anders"
SCHEMA = SchemaInfo(
    tables=(
        Table(
            name="customers",
            columns=(Column(name="contact_name", data_type="text"),),
            sample_rows=((SECRET,),),
        ),
    )
)


class FakeDatabase:
    """Stands in for reading the schema. Can fail, and counts how often it is read."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.reads = 0

    def __call__(self) -> tuple[SchemaInfo, Mapping[str, int | None]]:
        self.reads += 1
        if self.reads <= self.fail_times:
            raise DatabaseUnavailableError("Could not read the database's structure.")
        return SCHEMA, {"customers": 91}


def client(database: FakeDatabase) -> TestClient:
    explorer = ExplorerData(LAYERS, database)
    app = create_app(pipeline=object(), explorer=explorer)  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def test_catalog_gives_tables_with_their_business_and_count() -> None:
    with client(FakeDatabase()) as api:
        body = api.get("/catalog").json()

    assert body["tables"][0]["name"] == "customers"
    assert body["tables"][0]["business"] == "northwind"
    assert body["tables"][0]["row_count"] == 91


def test_a_sample_value_never_reaches_the_client() -> None:
    with client(FakeDatabase()) as api:
        catalog = api.get("/catalog")
        semantic = api.get("/semantic")

    assert SECRET not in catalog.text
    assert SECRET not in semantic.text


def test_semantic_gives_both_businesses_with_terms_and_flags() -> None:
    with client(FakeDatabase()) as api:
        body = api.get("/semantic").json()

    businesses = {business["database"]: business for business in body["businesses"]}
    assert set(businesses) == {"northwind", "chinook"}
    assert {metric["name"] for metric in businesses["chinook"]["metrics"]} >= {"music_sales"}
    customers = next(t for t in businesses["northwind"]["tables"] if t["name"] == "customers")
    contact = next(c for c in customers["columns"] if c["name"] == "contact_name")
    assert contact["pii"] is True


def test_an_example_s_sql_stays_out_of_the_answer() -> None:
    with client(FakeDatabase()) as api:
        text = api.get("/semantic").text

    assert "SELECT count(*)" not in text  # metric SQL is an expression, example SQL a whole query


def test_the_catalog_is_read_once() -> None:
    database = FakeDatabase()

    with client(database) as api:
        api.get("/catalog")
        api.get("/catalog")

    assert database.reads == 1


def test_with_the_database_down_catalog_says_so_and_semantic_still_works() -> None:
    with client(FakeDatabase(fail_times=1)) as api:
        catalog = api.get("/catalog")
        semantic = api.get("/semantic")

    assert catalog.status_code == 503
    assert catalog.json()["error"] == "db_unavailable"
    assert semantic.status_code == 200


def test_a_failed_read_isnt_kept() -> None:
    with client(FakeDatabase(fail_times=1)) as api:
        first = api.get("/catalog")
        second = api.get("/catalog")

    assert (first.status_code, second.status_code) == (503, 200)


def test_the_routes_are_in_the_api_schema() -> None:
    with client(FakeDatabase()) as api:
        paths = api.get("/openapi.json").json()["paths"]

    assert {"/catalog", "/semantic"} <= set(paths)
    assert "503" in paths["/catalog"]["get"]["responses"]


class FakeStore:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.asked: list[tuple[str, int]] = []

    def recent_questions_using(self, table: str, *, limit: int) -> list[TableQuestion]:
        if self.error is not None:
            raise self.error
        self.asked.append((table, limit))
        return [
            TableQuestion(
                conversation_id=uuid4(),
                n=2,
                question="Top customers?",
                created_at=datetime(2026, 11, 3, tzinfo=UTC),
            )
        ]


def client_with(store: FakeStore | None) -> TestClient:
    explorer = ExplorerData(LAYERS, FakeDatabase())
    app = create_app(pipeline=object(), store=store, explorer=explorer)  # type: ignore[arg-type]
    return TestClient(app, raise_server_exceptions=False)


def test_recent_questions_for_a_table() -> None:
    store = FakeStore()

    with client_with(store) as api:
        body = api.get("/catalog/tables/customers/questions").json()

    assert body["questions"][0]["question"] == "Top customers?"
    assert body["questions"][0]["n"] == 2
    assert store.asked == [("customers", 5)]


@pytest.mark.parametrize("path", ["questions?limit=0", "questions?limit=21"])
def test_recent_questions_refuse_a_silly_limit(path: str) -> None:
    with client_with(FakeStore()) as api:
        assert api.get(f"/catalog/tables/customers/{path}").status_code == 422


def test_recent_questions_refuse_a_name_longer_than_postgres_allows() -> None:
    with client_with(FakeStore()) as api:
        assert api.get(f"/catalog/tables/{'x' * 64}/questions").status_code == 422


@pytest.mark.parametrize(
    "store",
    [None, FakeStore(StoreUnavailableError("down"))],
    ids=["history off", "database down"],
)
def test_recent_questions_without_the_store_say_so(store: FakeStore | None) -> None:
    with client_with(store) as api:
        response = api.get("/catalog/tables/customers/questions")

    assert response.status_code == 503
    assert response.json()["error"] == "history_unavailable"
