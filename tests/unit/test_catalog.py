"""The explorer's two views of the data, built from what is already loaded. No DB, no LLM."""

from typing import Any

import psycopg

from querymind.agent.schemas import FewShotExample
from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table, read_row_counts
from querymind.semantic.catalog import MAX_SUGGESTED_QUESTIONS, build_catalog, build_overview
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.models import ColumnInfo, JoinPath, Metric, SemanticLayer, TableInfo

SECRET = "Maria Anders"
SCHEMA = SchemaInfo(
    tables=(
        Table(
            name="customers",
            columns=(
                Column(name="id", data_type="integer"),
                Column(name="contact", data_type="text"),
            ),
            primary_key=("id",),
            sample_rows=((1, SECRET),),
        ),
        Table(
            name="orders",
            columns=(
                Column(name="id", data_type="integer"),
                Column(name="cust", data_type="integer"),
            ),
            primary_key=("id",),
            foreign_keys=(
                ForeignKey(columns=("cust",), ref_table="customers", ref_columns=("id",)),
            ),
        ),
        Table(name="stray", columns=(Column(name="id", data_type="integer"),)),
    )
)
LAYER = SemanticLayer(
    database="shop",
    version=1,
    description="Our shop.",
    scope=("customers", "orders", "undescribed"),
    tables={
        "customers": TableInfo(
            description="People who buy.",
            synonyms=("clients",),
            columns={
                "contact": ColumnInfo(pii=True, description="Who to call.", synonyms=("rep",)),
            },
        ),
    },
    metrics={
        "revenue": Metric(
            kind="measure",
            table="orders",
            sql="sum(orders.total)",
            description="All the money.",
            synonyms=("turnover",),
        ),
    },
    joins=(JoinPath(left="orders.cust", right="customers.id", note="who bought"),),
)
DICTIONARY = DataDictionary(
    database="shop",
    tables={
        "customers": DictionaryTable(
            reviewed=True,
            description="One row per customer.",
            columns={"id": "The key.", "contact": "A contact name."},
        ),
        "orders": DictionaryTable(description="One row per order.", columns={"cust": "The buyer."}),
    },
)


def example(id_: str, question: str, sql: str) -> FewShotExample:
    return FewShotExample(id=id_, question=question, sql=sql)


def overview_table(name: str, examples: tuple[FewShotExample, ...] = ()):  # type: ignore[no-untyped-def]
    business = build_overview([LAYER], {"shop": DICTIONARY}, examples).businesses[0]
    return next(table for table in business.tables if table.name == name)


def test_the_catalog_never_copies_a_sample_value() -> None:
    catalog = build_catalog(SCHEMA, {"customers": 91}, [LAYER])

    assert SECRET not in catalog.model_dump_json()


def test_the_catalog_gives_each_table_its_structure_and_business() -> None:
    tables = {table.name: table for table in build_catalog(SCHEMA, {"orders": 830}, [LAYER]).tables}

    assert tables["orders"].business == "shop"
    assert tables["orders"].row_count == 830
    assert tables["orders"].primary_key == ["id"]
    assert tables["orders"].foreign_keys[0].ref_table == "customers"
    assert tables["customers"].columns[1].data_type == "text"


def test_a_table_no_layer_covers_has_no_business_and_a_missing_count_is_unknown() -> None:
    tables = {table.name: table for table in build_catalog(SCHEMA, {}, [LAYER]).tables}

    assert tables["stray"].business is None
    assert tables["stray"].row_count is None


def test_a_table_keeps_written_text_and_the_draft_apart() -> None:
    table = overview_table("customers")

    assert table.description == "People who buy."
    assert table.synonyms == ["clients"]
    assert table.dictionary_description == "One row per customer."
    assert table.dictionary_reviewed is True
    contact = next(column for column in table.columns if column.name == "contact")
    assert (contact.description, contact.dictionary_note) == ("Who to call.", "A contact name.")
    assert contact.synonyms == ["rep"]
    assert contact.pii is True


def test_a_draft_nobody_reviewed_says_so() -> None:
    assert overview_table("orders").dictionary_reviewed is False


def test_a_column_only_in_the_dictionary_is_listed_and_isnt_pii() -> None:
    column = overview_table("customers").columns[0]

    assert (column.name, column.pii, column.description) == ("id", False, "")


def test_a_table_in_scope_with_no_text_is_still_listed() -> None:
    table = overview_table("undescribed")

    assert (table.description, table.dictionary_description, table.columns) == ("", "", [])
    assert table.dictionary_reviewed is False


def test_a_missing_dictionary_is_fine() -> None:
    business = build_overview([LAYER], {}, []).businesses[0]

    assert [table.dictionary_description for table in business.tables] == ["", "", ""]


def test_metrics_and_joins_come_through() -> None:
    business = build_overview([LAYER], {}, []).businesses[0]

    assert business.description == "Our shop."
    assert business.metrics[0].sql == "sum(orders.total)"
    assert business.metrics[0].kind == "measure"
    assert business.metrics[0].synonyms == ["turnover"]
    assert (business.joins[0].left, business.joins[0].note) == ("orders.cust", "who bought")


def test_suggested_questions_put_the_ones_about_this_table_first() -> None:
    examples = (
        example("a", "Orders per customer?", "SELECT 1 FROM orders JOIN customers ON TRUE"),
        example("b", "How many customers?", "SELECT count(*) FROM customers"),
    )

    assert overview_table("customers", examples).suggested_questions == [
        "How many customers?",
        "Orders per customer?",
    ]
    assert overview_table("orders", examples).suggested_questions == ["Orders per customer?"]


def test_a_cte_name_isnt_taken_for_a_table() -> None:
    examples = (
        example(
            "a",
            "Big orders?",
            "WITH customers AS (SELECT 1 AS id FROM orders) SELECT * FROM customers",
        ),
    )

    assert overview_table("customers", examples).suggested_questions == []
    assert overview_table("orders", examples).suggested_questions == ["Big orders?"]


def test_an_example_the_validator_refuses_is_skipped() -> None:
    examples = (
        example("a", "Elsewhere?", "SELECT 1 FROM not_in_scope"),
        example("b", "Fine?", "SELECT 1 FROM orders"),
    )

    assert overview_table("orders", examples).suggested_questions == ["Fine?"]


def test_only_a_few_questions_per_table_and_never_the_example_s_sql() -> None:
    examples = tuple(
        example(f"e{n}", f"Question {n}?", f"SELECT {n} AS marker_{n} FROM orders")
        for n in range(9)
    )

    table = overview_table("orders", examples)

    assert len(table.suggested_questions) == MAX_SUGGESTED_QUESTIONS
    assert "marker_" not in table.model_dump_json()


class FakeConnection:
    """Answers the estimates query with `estimates`, and a count with `counts` (None: it fails)."""

    def __init__(self, estimates: dict[str, int], counts: dict[str, int | None]) -> None:
        self.estimates = estimates
        self.counts = counts
        self.counted: list[str] = []
        self.rollbacks = 0
        self.result: list[tuple[Any, ...]] = []

    def execute(self, query: Any, params: Any = None) -> "FakeConnection":
        if params is not None:  # only the estimates query has parameters
            self.result = list(self.estimates.items())
            return self
        table = bytes(query).decode().rsplit(".", 1)[1]
        self.counted.append(table)
        count = self.counts[table]
        if count is None:
            raise psycopg.errors.QueryCanceled("statement timeout")
        self.result = [(count,)]
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None

    def rollback(self) -> None:
        self.rollbacks += 1


def test_an_estimate_is_used_as_it_is_with_nothing_counted() -> None:
    conn = FakeConnection({"orders": 830, "empty": 0}, {})

    counts = read_row_counts(conn, ["orders", "empty"])  # type: ignore[arg-type]

    assert counts == {"orders": 830, "empty": 0}
    assert conn.counted == []


def test_no_estimate_means_a_real_count_of_only_that_table() -> None:
    conn = FakeConnection({"orders": 830, "genre": -1}, {"genre": 25})

    counts = read_row_counts(conn, ["orders", "genre"])  # type: ignore[arg-type]

    assert counts == {"orders": 830, "genre": 25}
    assert conn.counted == ["genre"]


def test_a_count_that_fails_is_unknown_and_the_next_table_still_counts() -> None:
    conn = FakeConnection({"huge": -1, "genre": -1}, {"huge": None, "genre": 25})

    counts = read_row_counts(conn, ["huge", "genre"])  # type: ignore[arg-type]

    assert counts == {"huge": None, "genre": 25}
    assert conn.rollbacks == 1  # without it, the failed statement would also fail the next one
