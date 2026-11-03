"""The explorer endpoints over the real YAML files, with the database replaced. No DB, no LLM."""

from collections.abc import Mapping
from pathlib import Path

from fastapi.testclient import TestClient

from querymind.api.explorer import ExplorerData
from querymind.api.main import create_app
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import Column, SchemaInfo, Table

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
