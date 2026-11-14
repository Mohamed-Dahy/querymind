"""The auto data dictionary: PII hidden from the LLM, reviewed entries kept, files round-trip."""

from pathlib import Path

import pytest

from querymind.db.schema import Column, SchemaInfo, Table
from querymind.llm.fake import FakeLLMProvider
from querymind.safety.pii import HIDDEN
from querymind.semantic.dictionary import (
    ColumnDraft,
    DataDictionary,
    DictionaryTable,
    TableDraft,
    dictionary_path,
    draft_table,
    load_dictionary,
    masked,
    merge_drafts,
    personal_columns,
    save_dictionary,
)
from querymind.semantic.loader import SemanticLayerError, load_semantic_layer
from querymind.semantic.models import SemanticLayer

CUSTOMER = Table(
    name="customer",
    columns=(
        Column(name="customer_id", data_type="integer"),
        Column(name="country", data_type="varchar"),
        Column(name="email", data_type="varchar"),
    ),
    primary_key=("customer_id",),
    sample_rows=((1, "Brazil", "luis@example.com"), (2, "Germany", "leonie@example.com")),
)
SCHEMA = SchemaInfo(tables=(CUSTOMER,))
LAYER = SemanticLayer.model_validate(
    {
        "database": "music",
        "version": 1,
        "description": "Our music store.",
        "tables": {"customer": {"columns": {"email": {"pii": True}}}},
    }
)
DRAFT = TableDraft(
    description=" People who buy music. ",
    columns=[
        ColumnDraft(name="customer_id", description="Unique id."),
        ColumnDraft(name="email", description="Contact email."),
        ColumnDraft(name="invented", description="Not a real column."),
    ],
)


def test_pii_values_never_reach_the_llm() -> None:
    llm = FakeLLMProvider([DRAFT])
    assert draft_table(llm, CUSTOMER, LAYER) == DRAFT
    prompt = llm.calls[0].prompt
    assert "luis@example.com" not in prompt and "leonie@example.com" not in prompt
    assert f"'{HIDDEN}'" in prompt
    assert "Brazil" in prompt  # other sample values still help the LLM understand the table
    assert "This table belongs to: Our music store." in prompt


def test_masking_leaves_tables_without_pii_alone() -> None:
    plain = SemanticLayer(database="music", version=1)
    genre = Table(
        name="genre",
        columns=(
            Column(name="genre_id", data_type="integer"),
            Column(name="name", data_type="text"),
        ),
        sample_rows=((1, "Rock"),),
    )
    assert masked(genre, plain) is genre


def test_a_column_named_like_personal_data_is_hidden_without_a_flag() -> None:
    # A saved database has no layer yet when onboarding drafts it (5.4): the name is enough.
    plain = SemanticLayer(database="music", version=1)
    assert masked(CUSTOMER, plain).sample_rows == ((1, "Brazil", HIDDEN), (2, "Germany", HIDDEN))


def test_a_draft_keeps_only_real_columns_in_table_order() -> None:
    merged = merge_drafts(
        None, {"customer": DRAFT}, database="music", schema=SCHEMA, generated_by="fake"
    )
    entry = merged.tables["customer"]
    assert entry == DictionaryTable(
        reviewed=False,
        description="People who buy music.",
        columns={"customer_id": "Unique id.", "country": "", "email": "Contact email."},
        pii=["email"],  # not flagged by the LLM, but named like personal data
    )
    assert merged.generated_by == "fake"


def test_a_draft_flags_the_llms_personal_columns_and_the_name_guesses() -> None:
    draft = DRAFT.model_copy(update={"personal_columns": ["country", "invented"]})
    merged = merge_drafts(
        None, {"customer": draft}, database="music", schema=SCHEMA, generated_by="fake"
    )
    assert merged.tables["customer"].pii == ["country", "email"]  # real columns, table order


def test_personal_columns_use_the_flags_or_else_the_names() -> None:
    orders = Table(
        name="orders",
        columns=(
            Column(name="order_id", data_type="int"),
            Column(name="ship_address", data_type="text"),
        ),
    )
    schema = SchemaInfo(tables=(CUSTOMER, orders))
    reviewed = DataDictionary(
        database="music",
        tables={"customer": DictionaryTable(pii=["country"]), "orders": DictionaryTable()},
    )

    assert personal_columns(None, schema) == {("customer", "email"), ("orders", "ship_address")}
    # customer: an admin's flags win (email unflagged); orders: drafted before 5.4, so guessed.
    assert personal_columns(reviewed, schema) == {
        ("customer", "country"),
        ("orders", "ship_address"),
    }
    cleared = reviewed.model_copy(update={"tables": {"customer": DictionaryTable(pii=[])}})
    assert ("customer", "email") not in personal_columns(cleared, schema)


def test_a_reviewed_table_is_never_overwritten() -> None:
    reviewed = DictionaryTable(reviewed=True, description="Checked by a person.")
    existing = DataDictionary(database="music", tables={"customer": reviewed})
    merged = merge_drafts(
        existing, {"customer": DRAFT}, database="music", schema=SCHEMA, generated_by="fake"
    )
    assert merged.tables["customer"] == reviewed


def test_the_file_round_trips(tmp_path: Path) -> None:
    path = dictionary_path(tmp_path / "music.yaml")
    assert path.name == "music.dictionary.yaml"
    assert load_dictionary(path) is None  # nothing drafted yet
    dictionary = merge_drafts(
        None, {"customer": DRAFT}, database="music", schema=SCHEMA, generated_by="fake"
    )
    save_dictionary(path, dictionary)
    assert path.read_text(encoding="utf-8").startswith("# Data dictionary drafted by an LLM")
    assert load_dictionary(path) == dictionary


def test_a_broken_dictionary_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "music.dictionary.yaml"
    path.write_text("database: music\ntables:\n  customer: {reviewd: true}\n", encoding="utf-8")
    with pytest.raises(SemanticLayerError, match="not a valid data dictionary"):
        load_dictionary(path)


def test_the_shipped_dictionaries_cover_every_table_in_scope() -> None:
    layers_dir = Path(__file__).parents[2] / "semantic_layer"
    for name in ("northwind", "chinook"):
        layer = load_semantic_layer(layers_dir / f"{name}.yaml")
        dictionary = load_dictionary(dictionary_path(layers_dir / f"{name}.yaml"))
        assert dictionary is not None
        assert set(dictionary.tables) == set(layer.table_scope), name
