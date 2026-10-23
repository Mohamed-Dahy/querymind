"""The documents schema retrieval searches (3.3): what goes into each one, and from where."""

from querymind.semantic.documents import build_documents
from tests.unit.retrieval_fixtures import DICTIONARIES, FOOD, MUSIC, SCHEMA

DOCS = {doc.id: doc for doc in build_documents(SCHEMA, [FOOD, MUSIC], DICTIONARIES)}


def test_one_document_per_table_and_per_metric() -> None:
    tables = {d.name for d in DOCS.values() if d.kind == "table"}
    assert tables == set(SCHEMA.table_names)  # audit_notes too, though no layer covers it
    assert {d.id for d in DOCS.values() if d.kind == "metric"} == {
        "metric:revenue",
        "metric:big_spender",
    }


def test_a_table_document_holds_what_people_search_with() -> None:
    text = DOCS["table:customers"].text
    assert text.startswith("Table customers (Our food trading business)")
    assert "Companies that buy our food products." in text  # hand-written beats the draft
    assert "LLM draft" not in text
    assert "Also called: clients" in text
    assert "company_name (Legal name, also called client name)" in text
    assert "country" in text  # a column nobody described still shows its name


def test_the_dictionary_fills_what_the_layer_leaves_out() -> None:
    text = DOCS["table:shippers"].text
    assert "Delivery companies that ship orders." in text
    assert "company_name (Name of the delivery company)" in text


def test_business_terms_point_at_their_table() -> None:
    revenue = DOCS["metric:revenue"]
    assert revenue.table == "order_details"
    assert "revenue (also: turnover): Money from food sales." in revenue.text
    assert "Business terms: revenue (Money from food sales)" in DOCS["table:order_details"].text


def test_the_two_customer_tables_say_which_business_they_belong_to() -> None:
    assert "(Our food trading business)" in DOCS["table:customers"].text
    assert "(Our online music store)" in DOCS["table:customer"].text
    assert DOCS["table:audit_notes"].text.startswith("Table audit_notes\n")


def test_the_hash_follows_the_text() -> None:
    again = {d.id: d for d in build_documents(SCHEMA, [FOOD, MUSIC], DICTIONARIES)}
    assert again["table:customers"].hash == DOCS["table:customers"].hash
    without_dictionaries = {d.id: d for d in build_documents(SCHEMA, [FOOD, MUSIC], {})}
    assert without_dictionaries["table:shippers"].hash != DOCS["table:shippers"].hash
    assert without_dictionaries["table:customers"].hash == DOCS["table:customers"].hash
