"""Schema retrieval (3.3): which tables reach the prompt, and what happens when search fails.

The search itself is stubbed (it's pgvector SQL, tested in tests/integration/test_schema_index.py),
so these tests check the selection rules: top-k, carry-over, join bridges, the cap, fallbacks.
"""

from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import SecretStr

from querymind.agent.schemas import Turn
from querymind.config import Settings
from querymind.llm import LLMProviderError
from querymind.llm.embeddings import FakeEmbeddings
from querymind.semantic import retrieval as retrieval_module
from querymind.semantic.index import IndexHit
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever
from tests.unit.retrieval_fixtures import FOOD, MUSIC, SCHEMA

CATALOG = SchemaCatalog(SCHEMA, [FOOD, MUSIC])


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "llm_provider": "groq",
        "groq_api_key": SecretStr("gsk_test"),
        "groq_model": "openai/gpt-oss-120b",
        "retrieval_top_k": 2,
        "retrieval_max_tables": 8,
    }
    return Settings.model_validate(values | overrides)


def hit(doc: str, similarity: float = 0.5) -> IndexHit:
    kind, name = doc.split(":")
    table = {"revenue": "order_details", "big_spender": "customer"}.get(name, name)
    return IndexHit(id=doc, kind=kind, table=table, similarity=similarity)


def retriever(
    monkeypatch: pytest.MonkeyPatch, *found: IndexHit | Exception, **overrides: Any
) -> SchemaRetriever:
    """A retriever whose search returns `found` (or raises it, if it's one exception)."""

    def fake_search(conn: psycopg.Connection, vector: list[float], **_: Any) -> list[IndexHit]:
        if len(found) == 1 and isinstance(found[0], Exception):
            raise found[0]
        return [h for h in found if isinstance(h, IndexHit)]

    monkeypatch.setattr(retrieval_module, "search", fake_search)
    return SchemaRetriever(
        catalog=CATALOG,
        embeddings=FakeEmbeddings(),
        connect=MagicMock(),
        settings=settings(**overrides),
    )


# --- The join graph --------------------------------------------------------------------------


def test_bridges_add_the_tables_a_join_needs() -> None:
    # customers and products only meet through orders → order_details.
    assert CATALOG.bridges(["customers", "products"]) == ["orders", "order_details"]
    assert CATALOG.bridges(["orders", "customers"]) == []  # already joined directly


def test_no_bridge_between_the_two_businesses() -> None:
    assert CATALOG.bridges(["customers", "customer"]) == []


def test_neighbours_are_one_join_away() -> None:
    assert CATALOG.neighbours(["orders"]) == {"customers", "shippers", "order_details"}


def test_a_subset_text_has_only_those_tables_and_their_definitions() -> None:
    text = CATALOG.text(["customer", "invoice"])
    assert "Table customer\n" in text and "Table invoice\n" in text
    assert "Table customers" not in text and "Table orders" not in text
    assert "big_spender" in text  # the music store's terms...
    assert "revenue" not in text and "food trading" not in text  # ...not the food business's


# --- Selection -----------------------------------------------------------------------------


def test_top_k_distinct_tables_best_first(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(
        monkeypatch,
        hit("table:customer", 0.9),
        hit("metric:big_spender", 0.8),
        hit("table:invoice", 0.7),
    )
    selection = r.select("Who are our big spenders?")
    # big_spender points at customer, already found: the second distinct table is invoice.
    assert selection.info.mode == "retrieved"
    assert selection.info.hits == ("customer", "invoice")
    assert selection.info.tables == ("customer", "invoice")
    assert (
        "Table customer\n" in selection.schema_text and "Table orders" not in selection.schema_text
    )


def test_a_metric_hit_brings_its_table(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(monkeypatch, hit("metric:revenue"), hit("table:products"))
    assert r.select("Turnover per product").info.hits == ("order_details", "products")


def test_join_bridges_are_added_after_the_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(monkeypatch, hit("table:customers"), hit("table:products"))
    info = r.select("Which products does each client buy?").info
    assert info.tables == ("customers", "products", "orders", "order_details")
    assert info.added == ("orders", "order_details")


def test_a_follow_up_keeps_the_previous_answers_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    previous = Turn(
        question="Revenue per shipper",
        kind="answer",
        sql="WITH s AS (SELECT ship_via FROM orders) SELECT * FROM s JOIN shippers ON true",
        columns=["ship_via"],
        row_count=3,
    )
    r = retriever(monkeypatch, hit("table:products"), hit("table:customer"))
    info = r.select("Only for 1997", history=[previous]).info
    assert info.hits == ("products", "customer")  # what the search alone found: no orders
    assert {"orders", "shippers"} <= set(info.tables)  # carried over (s is a CTE, not a table)
    assert "s" not in info.tables


def test_a_shown_metric_brings_the_tables_its_sql_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    # big_spender is a term on customer, but its SQL reads invoice: both must be in the prompt.
    r = retriever(monkeypatch, hit("table:customer"), retrieval_top_k=1)
    info = r.select("Music customers in Brazil").info
    assert info.hits == ("customer",)
    assert info.tables == ("customer", "invoice")


def test_the_cap_keeps_the_best_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(
        monkeypatch, hit("table:customers"), hit("table:products"), retrieval_max_tables=3
    )
    assert r.select("Products per client").info.tables == ("customers", "products", "orders")


def test_a_repair_sees_the_neighbours_too(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(monkeypatch, hit("table:orders"), retrieval_top_k=1)
    selection = r.select("Orders per shipper")
    assert selection.info.tables == ("orders",)
    assert "Table shippers" not in selection.schema_text
    # If the first SQL fails, the repair can still find the table retrieval left out.
    assert "Table shippers" in selection.repair_schema_text
    assert "Table customer\n" not in selection.repair_schema_text  # still not everything


def test_the_selection_reports_its_size(monkeypatch: pytest.MonkeyPatch) -> None:
    r = retriever(monkeypatch, hit("table:shippers"), retrieval_top_k=1)
    info = r.select("List the shippers").info
    assert 0 < info.schema_tokens < CATALOG.full(mode="full").info.schema_tokens


# --- Fallbacks: never an empty schema, never silent ---------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        LLMProviderError("gemini-embedding-001 embedding request failed: 429"),
        psycopg.OperationalError("gone"),
    ],
)
def test_a_failed_search_sends_the_full_schema_and_says_so(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    selection = retriever(monkeypatch, failure).select("How many customers?")
    assert selection.info.mode == "fallback"
    assert type(failure).__name__ in selection.info.reason
    assert selection.schema_text == CATALOG.full_text
    assert set(selection.info.tables) == SCHEMA.table_names


def test_an_empty_index_falls_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = retriever(monkeypatch).select("How many customers?")
    assert selection.info.mode == "fallback"
    assert selection.info.reason == "the index returned no documents"
