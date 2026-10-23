"""Schema retrieval (3.3, 3.4): which tables reach the prompt, and what happens when a step fails.

Both searches are stubbed (they're SQL, tested in tests/integration/test_schema_index.py), so
these tests check the selection rules: top-k, fusion, rerank, carry-over, metric needs, join
bridges, the cap, and every fallback.
"""

from collections.abc import Sequence
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from pydantic import SecretStr

from querymind.agent.schemas import Turn
from querymind.config import Settings
from querymind.llm import LLMOutputError, LLMProviderError
from querymind.llm.embeddings import EmbeddingProvider, FakeEmbeddings
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic import retrieval as retrieval_module
from querymind.semantic.index import IndexHit
from querymind.semantic.retrieval import (
    RRF_K,
    RerankResult,
    SchemaCatalog,
    SchemaRetriever,
    fuse,
)
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


def hit(doc: str, score: float = 0.5) -> IndexHit:
    kind, name = doc.split(":")
    table = {"revenue": "order_details", "big_spender": "customer"}.get(name, name)
    return IndexHit(id=doc, kind=kind, table=table, score=score)


class BrokenEmbeddings(FakeEmbeddings):
    """The embedding API is down (a 429, a timeout...)."""

    def embed_query(self, text: str) -> list[float]:
        raise LLMProviderError("gemini-embedding-001 embedding request failed: 429")


def retriever(
    monkeypatch: pytest.MonkeyPatch,
    *found: IndexHit | Exception,
    words: Sequence[IndexHit] = (),
    embeddings: EmbeddingProvider | None = None,
    llm: FakeLLMProvider | None = None,
    **overrides: Any,
) -> SchemaRetriever:
    """A retriever whose vector search returns `found` (or raises it, if it's one exception)
    and whose keyword search returns `words`. Vector only by default, as in 3.3."""

    def fake_search(conn: psycopg.Connection, vector: list[float], **_: Any) -> list[IndexHit]:
        if len(found) == 1 and isinstance(found[0], Exception):
            raise found[0]
        return [h for h in found if isinstance(h, IndexHit)]

    monkeypatch.setattr(retrieval_module, "search", fake_search)
    monkeypatch.setattr(retrieval_module, "keyword_search", lambda conn, q, **_: list(words))
    return SchemaRetriever(
        catalog=CATALOG,
        embeddings=embeddings or FakeEmbeddings(),
        connect=MagicMock(),
        settings=settings(**{"retrieval_mode": "vector", **overrides}),
        llm=llm,
        documents={"customer": "Table customer: people who buy music."},
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


def test_a_failed_search_sends_the_full_schema_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = psycopg.OperationalError("gone")
    selection = retriever(monkeypatch, failure, retrieval_mode="hybrid").select("How many?")
    assert selection.info.mode == "fallback"
    assert "OperationalError" in selection.info.reason
    assert selection.schema_text == CATALOG.full_text
    assert set(selection.info.tables) == SCHEMA.table_names


def test_an_empty_index_falls_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    selection = retriever(monkeypatch).select("How many customers?")
    assert selection.info.mode == "fallback"
    assert selection.info.reason == "the search found no matching documents"


@pytest.mark.parametrize("mode", ["vector", "hybrid", "hybrid_rerank"])
def test_without_embeddings_keyword_search_carries_on(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    r = retriever(
        monkeypatch,
        hit("table:customers"),  # never reached: the question can't be embedded
        words=[hit("table:shippers")],
        embeddings=BrokenEmbeddings(),
        llm=FakeLLMProvider(),  # and no rerank call is made either
        retrieval_mode=mode,
        retrieval_top_k=1,
    )
    info = r.select("List the shippers").info
    assert info.mode == "retrieved" and info.search == "keyword"
    assert info.hits == ("shippers",)
    assert info.reason.startswith("embeddings failed, keyword search only")


def test_no_embeddings_and_no_matching_words_is_a_full_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    r = retriever(monkeypatch, embeddings=BrokenEmbeddings(), retrieval_mode="hybrid")
    info = r.select("What is it?").info
    assert info.mode == "fallback"
    assert info.reason.startswith("embeddings failed")


# --- Hybrid search: Reciprocal Rank Fusion (3.4) ----------------------------------------------


def test_fusion_adds_up_reciprocal_ranks() -> None:
    # b is 2nd by meaning and 1st by words: in both lists beats first in one.
    assert fuse([["a", "b"], ["b", "c"]]) == ["b", "a", "c"]
    scores = {"a": 1 / (RRF_K + 1), "b": 1 / (RRF_K + 2) + 1 / (RRF_K + 1), "c": 1 / (RRF_K + 2)}
    assert sorted(scores, key=lambda t: -scores[t]) == ["b", "a", "c"]


def test_fusion_ties_are_stable() -> None:
    # Same score: the better single rank wins, then the name. Same answer every run.
    assert fuse([["b"], ["a"]]) == ["a", "b"]
    assert fuse([["x", "a"], ["b"]]) == ["b", "x", "a"]
    assert fuse([[], []]) == []


def test_keyword_mode_never_embeds(monkeypatch: pytest.MonkeyPatch) -> None:
    embeddings = FakeEmbeddings()
    r = retriever(
        monkeypatch,
        words=[hit("table:products")],
        embeddings=embeddings,
        retrieval_mode="keyword",
        retrieval_top_k=1,
    )
    info = r.select("product names").info
    assert info.hits == ("products",) and info.search == "keyword"
    assert embeddings.calls == 0


def test_hybrid_finds_what_one_search_alone_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    # Meaning finds customers first; the exact word "shipper" finds shippers. Both make it.
    r = retriever(
        monkeypatch,
        hit("table:customers"),
        hit("table:orders"),
        words=[hit("table:shippers"), hit("table:orders")],
        retrieval_mode="hybrid",
        retrieval_top_k=3,
    )
    info = r.select("Which shipper delivered the most client orders?").info
    assert info.search == "hybrid"
    assert info.hits == ("orders", "customers", "shippers")  # orders is in both lists


# --- Rerank (3.4, an experiment arm) -------------------------------------------------------------


def rerank_retriever(monkeypatch: pytest.MonkeyPatch, llm: FakeLLMProvider) -> SchemaRetriever:
    return retriever(
        monkeypatch,
        hit("table:customers"),
        hit("table:customer"),
        words=[hit("table:invoice")],
        llm=llm,
        retrieval_mode="hybrid_rerank",
        retrieval_top_k=2,
    )


def test_the_reranker_reorders_the_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLMProvider([RerankResult(tables=["customer", "invoice", "customers"])])
    info = rerank_retriever(monkeypatch, llm).select("Music customers per invoice").info
    assert info.search == "hybrid_rerank"
    assert info.hits == ("customer", "invoice")  # the food business's customers moved down
    (call,) = llm.calls
    assert "<question>Music customers per invoice</question>" in call.prompt
    assert '<table name="customer">\nTable customer: people who buy music.\n</table>' in call.prompt


def test_the_reranker_can_only_reorder(monkeypatch: pytest.MonkeyPatch) -> None:
    # An invented table is dropped; candidates it left out keep their fused order after it.
    llm = FakeLLMProvider([RerankResult(tables=["albums", "invoice"])])
    info = rerank_retriever(monkeypatch, llm).select("Invoices").info
    assert info.hits == ("invoice", "customers")
    assert "albums" not in info.tables


def test_a_failed_rerank_keeps_the_fused_order(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = FakeLLMProvider([LLMOutputError("not a RerankResult")])
    info = rerank_retriever(monkeypatch, llm).select("Invoices").info
    assert info.mode == "retrieved" and info.search == "hybrid"
    assert info.hits == ("customers", "invoice")  # fused: rank 1 in each list, then the name
    assert info.reason.startswith("rerank failed")


def test_rerank_needs_an_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="needs an LLM"):
        retriever(monkeypatch, retrieval_mode="hybrid_rerank")
