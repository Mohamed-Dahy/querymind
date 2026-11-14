"""The auto data dictionary (3.2): the LLM drafts a description of every table and column,
and a person reviews it.

    draft = draft_table(llm, table, layer)                   # one LLM call per table
    dictionary = merge_drafts(existing, {"customers": draft}, ...)
    save_dictionary(path, dictionary)        # semantic_layer/northwind.dictionary.yaml

Drafts are saved with `reviewed: false`. A person reads them, fixes what's wrong and sets
`reviewed: true`; a reviewed table is never overwritten by a new draft. The descriptions feed
schema retrieval (3.3), so they are not added to the full-schema prompt. A user's own database
(5.10) has no hand-written layer: its dictionary *becomes* the layer (`layer_from_dictionary`).

The LLM sees the table's columns, keys and 3 sample rows, but never the values of PII columns
(they are replaced by <hidden>): personal data shouldn't leave just to describe a column. A PII
column is one the layer flags, or one whose name says so (`likely_pii`: email, home_phone...).

PII flags (5.4): the LLM also lists the table's personal columns; each table's `pii` is that list
plus the name guesses, for an admin to correct. `pii: null` (a table drafted before 5.4) means not
flagged yet: the name guesses apply (`personal_columns`).
"""

import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from querymind.agent.prompts.data_dictionary import SYSTEM_PROMPT, build_user_prompt
from querymind.db.schema import SchemaInfo, Table, describe_schema
from querymind.llm import LLMProvider
from querymind.safety.pii import hide_samples, likely_pii
from querymind.semantic.loader import SemanticLayerError
from querymind.semantic.models import NAME, ColumnInfo, SemanticLayer, TableInfo

# --- What the LLM returns (a list, not a dict: structured output needs fixed field names) -----


class ColumnDraft(BaseModel):
    name: str
    description: str = Field(description="One short phrase: what this column holds")


class TableDraft(BaseModel):
    description: str = Field(description="One sentence: what one row is, and what it's used for")
    columns: list[ColumnDraft]
    personal_columns: list[str] = Field(
        default=[],
        description="The columns holding personal data about a person (names, contact details, "
        "addresses, birth dates, ids); empty if none",
    )


# --- What is saved -----------------------------------------------------------------------------


class DictionaryTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewed: bool = False  # a person checked it; set by hand in the YAML
    description: str = ""
    columns: dict[str, str] = {}
    # 5.4: the columns holding personal data, masked for every role but admin. None = not
    # flagged yet (drafted before 5.4): the column names are guessed from instead.
    pii: list[str] | None = None


class DataDictionary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str
    generated_by: str = ""
    tables: dict[str, DictionaryTable] = {}


def dictionary_path(layer_path: Path) -> Path:
    """semantic_layer/northwind.yaml → semantic_layer/northwind.dictionary.yaml"""
    return layer_path.with_name(f"{layer_path.stem}.dictionary.yaml")


def masked(table: Table, layer: SemanticLayer) -> Table:
    """The table with the sample values of its PII columns (flagged, or named like it) hidden."""
    pii = {(name, column) for name, column in layer.pii_columns if name == table.name}
    pii |= {(table.name, c.name) for c in table.columns if likely_pii(c.name)}
    if not pii or not table.sample_rows:
        return table
    return hide_samples(SchemaInfo(tables=(table,)), pii).tables[0]


def personal_columns(
    dictionary: DataDictionary | None, schema: SchemaInfo
) -> frozenset[tuple[str, str]]:
    """A saved database's personal columns (5.4): each table's reviewed or drafted flags, or the
    name guesses for a table not flagged yet (no dictionary, a new table, drafted before 5.4)."""
    entries = dictionary.tables if dictionary else {}
    found: set[tuple[str, str]] = set()
    for table in schema.tables:
        entry = entries.get(table.name)
        flagged = entry.pii if entry is not None else None
        for column in table.columns:
            if column.name in flagged if flagged is not None else likely_pii(column.name):
                found.add((table.name, column.name))
    return frozenset(found)


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
        flagged = set(draft.personal_columns)
        tables[name] = DictionaryTable(
            description=draft.description.strip(),
            columns={column: described.get(column, "") for column in columns[name]},
            # The LLM's flags plus the name guesses: a missed email is worse than one too many.
            pii=[c for c in columns[name] if c in flagged or likely_pii(c)],
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


def load_dictionaries(layer_paths: Sequence[Path]) -> dict[str, DataDictionary]:
    """The dictionary next to each layer file, keyed by its database. Missing files are skipped."""
    found = (load_dictionary(dictionary_path(path)) for path in layer_paths)
    return {d.database: d for d in found if d is not None}


HEADER = """\
# Data dictionary drafted by an LLM (scripts/draft_dictionary.py), for a person to review.
# Read each table, fix anything wrong, then set `reviewed: true`: reviewed tables are never
# overwritten by a new draft. Used by schema retrieval (Phase 3.3).
"""


def save_dictionary(path: Path, dictionary: DataDictionary) -> None:
    body = yaml.safe_dump(dictionary.model_dump(), sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")


def layer_from_dictionary(
    database: str, description: str, dictionary: DataDictionary, schema: SchemaInfo
) -> tuple[SemanticLayer, list[str]]:
    """A user database's dictionary as its semantic layer (5.10): the descriptions then reach the
    prompt, retrieval documents and `sources` like a hand-written layer's.

    Built against the schema *as it is now*: a table or column the user has since dropped is left
    out (a dictionary drafted last week must not break every question). Names that aren't plain
    lowercase SQL names can't be described in a layer; they are returned, to be reported.
    """
    columns_now = {table.name: {column.name for column in table.columns} for table in schema.tables}
    tables: dict[str, TableInfo] = {}
    skipped: list[str] = []
    for name, entry in dictionary.tables.items():
        if name not in columns_now:
            continue  # gone from the database
        if not re.fullmatch(NAME, name):
            skipped.append(name)
            continue
        described = {
            column: ColumnInfo(description=text)
            for column, text in entry.columns.items()
            if text and column in columns_now[name] and re.fullmatch(NAME, column)
        }
        skipped += [
            f"{name}.{column}"
            for column in entry.columns
            if column in columns_now[name] and not re.fullmatch(NAME, column)
        ]
        if entry.description or described:
            tables[name] = TableInfo(description=entry.description, columns=described)
    layer = SemanticLayer(database=database, version=1, description=description, tables=tables)
    return layer, skipped
