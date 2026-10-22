"""The auto data dictionary: PII hidden from the LLM, reviewed entries kept, files round-trip."""

from pathlib import Path

import pytest

from querymind.db.schema import Column, SchemaInfo, Table
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.dictionary import (
    HIDDEN,
    ColumnDraft,
    DataDictionary,
    DictionaryTable,
    TableDraft,
    dictionary_path,
    draft_table,
    load_dictionary,
    masked,
    merge_drafts,
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
    assert masked(CUSTOMER, plain) is CUSTOMER


def test_a_draft_keeps_only_real_columns_in_table_order() -> None:
    merged = merge_drafts(
        None, {"customer": DRAFT}, database="music", schema=SCHEMA, generated_by="fake"
    )
    entry = merged.tables["customer"]
    assert entry == DictionaryTable(
        reviewed=False,
        description="People who buy music.",
        columns={"customer_id": "Unique id.", "country": "", "email": "Contact email."},
    )
    assert merged.generated_by == "fake"


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
