"""The auto data dictionary (3.2): the LLM drafts a description of every table and column,
and a person reviews it.

    draft = draft_table(llm, table, layer)                   # one LLM call per table
    dictionary = merge_drafts(existing, {"customers": draft}, ...)
    save_dictionary(path, dictionary)        # semantic_layer/northwind.dictionary.yaml

Drafts are saved with `reviewed: false`. A person reads them, fixes what's wrong and sets
`reviewed: true`; a reviewed table is never overwritten by a new draft. The descriptions feed
schema retrieval (3.3), so they are not added to the full-schema prompt.

The LLM sees the table's columns, keys and 3 sample rows, but never the values of PII columns
(they are replaced by <hidden>): personal data shouldn't leave just to describe a column.
"""

from collections.abc import Mapping
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from querymind.agent.prompts.data_dictionary import SYSTEM_PROMPT, build_user_prompt
from querymind.db.schema import SchemaInfo, Table, describe_schema
from querymind.llm import LLMProvider
from querymind.semantic.loader import SemanticLayerError
from querymind.semantic.models import SemanticLayer

HIDDEN = "<hidden>"


# --- What the LLM returns (a list, not a dict: structured output needs fixed field names) -----


class ColumnDraft(BaseModel):
    name: str
    description: str = Field(description="One short phrase: what this column holds")


class TableDraft(BaseModel):
    description: str = Field(description="One sentence: what one row is, and what it's used for")
    columns: list[ColumnDraft]


# --- What is saved -----------------------------------------------------------------------------


class DictionaryTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed: bool = False  # a person checked it; set by hand in the YAML
    description: str = ""
    columns: dict[str, str] = {}


class DataDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    generated_by: str = ""
    tables: dict[str, DictionaryTable] = {}


def dictionary_path(layer_path: Path) -> Path:
    """semantic_layer/northwind.yaml → semantic_layer/northwind.dictionary.yaml"""
    return layer_path.with_name(f"{layer_path.stem}.dictionary.yaml")


def masked(table: Table, layer: SemanticLayer) -> Table:
    """The table with the sample values of its PII columns replaced by <hidden>."""
    pii = {column for name, column in layer.pii_columns if name == table.name}
    if not pii or not table.sample_rows:
        return table
    rows = tuple(
        tuple(
            HIDDEN if column.name in pii else value
            for column, value in zip(table.columns, row, strict=True)
        )
        for row in table.sample_rows
    )
    return table.model_copy(update={"sample_rows": rows})


def draft_table(llm: LLMProvider, table: Table, layer: SemanticLayer) -> TableDraft:
    """One LLM call: a description of `table` and each of its columns.

    Raises:
        LLMProviderError / LLMOutputError: from the LLM interface, passed through unchanged.
    """
    text = describe_schema(SchemaInfo(tables=(masked(table, layer),)))
    prompt = build_user_prompt(about=layer.description or layer.database, table_text=text)
    return llm.generate_structured(prompt, TableDraft, system=SYSTEM_PROMPT)


def merge_drafts(
    existing: DataDictionary | None,
    drafts: Mapping[str, TableDraft],
    *,
    database: str,
    schema: SchemaInfo,
    generated_by: str,
) -> DataDictionary:
    """New drafts replace unreviewed entries; reviewed ones are kept exactly as they are.

    Only real columns are kept, in table order: the LLM can't add a column that doesn't exist.
    """
    tables = dict(existing.tables) if existing else {}
    columns = {table.name: [c.name for c in table.columns] for table in schema.tables}
    for name, draft in drafts.items():
        if name in tables and tables[name].reviewed:
            continue
        described = {c.name: c.description.strip() for c in draft.columns}
        tables[name] = DictionaryTable(
            description=draft.description.strip(),
            columns={column: described.get(column, "") for column in columns[name]},
        )
    return DataDictionary(database=database, generated_by=generated_by, tables=tables)


def load_dictionary(path: Path) -> DataDictionary | None:
    """The saved dictionary, or None if there isn't one yet.

    Raises:
        SemanticLayerError: the file exists but isn't valid YAML or has the wrong shape.
    """
    if not path.exists():
        return None
    try:
        return DataDictionary.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise SemanticLayerError(f"{path} is not a valid data dictionary:\n{exc}") from exc


HEADER = """\
# Data dictionary drafted by an LLM (scripts/draft_dictionary.py), for a person to review.
# Read each table, fix anything wrong, then set `reviewed: true`: reviewed tables are never
# overwritten by a new draft. Used by schema retrieval (Phase 3.3).
"""


def save_dictionary(path: Path, dictionary: DataDictionary) -> None:
    body = yaml.safe_dump(dictionary.model_dump(), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")
