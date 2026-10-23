"""The texts schema retrieval searches (3.3): one document per table, one per metric.

    docs = build_documents(schema, layers, dictionaries)
    docs[0].text   # "Table customer (music store) ... Also called: clients ... Columns: ..."

A table document says what a person would search with: what the table is, its other names,
its columns and what they hold, and the business terms computed on it. The words come from the
semantic layer first (hand-written) and the data dictionary second (LLM draft, 3.2).

A metric gets its own document too, so "Who are our big spenders?" can find `big_spender`
even though no table or column has those words. A metric hit counts as a hit on its table.

A few-shot example (3.5) is a document too: its text is the example's *question*, so a new
question finds the examples asked the most alike. Searches filter by kind, so an example never
takes a table's place (and the other way round).

Each document has a hash of its text: the index only re-embeds documents whose text changed.

    docs = index_documents(schema, layers, settings.semantic_layer_paths)   # everything indexed
"""

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from querymind.agent.schemas import FewShotExample
from querymind.db.schema import SchemaInfo, Table
from querymind.semantic.dictionary import DataDictionary, DictionaryTable, load_dictionaries
from querymind.semantic.examples import load_all_examples
from querymind.semantic.models import Metric, SemanticLayer, TableInfo

SCHEMA_KINDS = ("table", "metric")  # what schema search ranks
EXAMPLE_KINDS = ("example",)  # what few-shot search ranks


class SchemaDoc(BaseModel):
    """One searchable text, and the table a hit on it brings into the prompt."""

    model_config = ConfigDict(frozen=True)

    id: str  # "table:customer", "metric:big_spender" or "example:cx09_jazz_buyers"
    kind: Literal["table", "metric", "example"]
    name: str
    table: str  # the table itself, the table a metric is computed on, "" for an example
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def build_documents(
    schema: SchemaInfo,
    layers: Sequence[SemanticLayer],
    dictionaries: Mapping[str, DataDictionary],
) -> list[SchemaDoc]:
    """Documents for every table the role can read, plus every metric on those tables.

    `dictionaries` is keyed by the layer's `database`. A table no layer covers still gets a
    document, from its columns alone.
    """
    owner = {table: layer for layer in layers for table in layer.table_scope}
    docs = []
    for table in schema.tables:
        layer = owner.get(table.name)
        dictionary = dictionaries.get(layer.database) if layer else None
        entry = dictionary.tables.get(table.name) if dictionary else None
        docs.append(_table_doc(table, layer, entry))
    visible = schema.table_names
    for layer in layers:
        for name, metric in layer.metrics.items():
            if metric.table in visible:
                docs.append(_metric_doc(name, metric, layer))
    return docs


def example_documents(examples: Sequence[FewShotExample]) -> list[SchemaDoc]:
    """One document per example; its text is the question (what a new question is like)."""
    return [
        SchemaDoc(id=f"example:{e.id}", kind="example", name=e.id, table="", text=e.question)
        for e in examples
    ]


def index_documents(
    schema: SchemaInfo, layers: Sequence[SemanticLayer], layer_paths: Sequence[Path]
) -> list[SchemaDoc]:
    """Everything the index holds: tables, metrics (with the dictionaries) and examples.

    One function for the build script, the app's freshness check and the evals, so they can't
    disagree about what "up to date" means.
    """
    dictionaries = load_dictionaries(layer_paths)
    examples = load_all_examples(layer_paths)
    return build_documents(schema, layers, dictionaries) + example_documents(examples)


def _table_doc(
    table: Table, layer: SemanticLayer | None, entry: DictionaryTable | None
) -> SchemaDoc:
    info = layer.tables.get(table.name, TableInfo()) if layer else TableInfo()
    about = f" ({layer.description.rstrip('.')})" if layer and layer.description else ""
    lines = [f"Table {table.name}{about}"]
    if description := info.description or (entry.description if entry else ""):
        lines.append(description)
    if info.synonyms:
        lines.append(f"Also called: {', '.join(info.synonyms)}")
    lines.append(
        "Columns: " + "; ".join(_column(table.name, c.name, info, entry) for c in table.columns)
    )
    if layer:
        terms = [
            f"{n} ({m.description.rstrip('.')})"
            for n, m in layer.metrics.items()
            if m.table == table.name
        ]
        if terms:
            lines.append(f"Business terms: {', '.join(terms)}")
    return SchemaDoc(
        id=f"table:{table.name}",
        kind="table",
        name=table.name,
        table=table.name,
        text="\n".join(lines),
    )


def _column(table: str, column: str, info: TableInfo, entry: DictionaryTable | None) -> str:
    """`name (what it holds, also called ...)`, or just the name when nothing is known."""
    hand = info.columns.get(column)
    parts = []
    if description := (hand.description if hand else "") or (
        entry.columns.get(column, "") if entry else ""
    ):
        parts.append(description.rstrip("."))
    if hand and hand.synonyms:
        parts.append(f"also called {', '.join(hand.synonyms)}")
    return f"{column} ({', '.join(parts)})" if parts else column


def _metric_doc(name: str, metric: Metric, layer: SemanticLayer) -> SchemaDoc:
    also = f" (also: {', '.join(metric.synonyms)})" if metric.synonyms else ""
    text = f"Business term {name}{also}: {metric.description} Computed on table {metric.table}."
    return SchemaDoc(id=f"metric:{name}", kind="metric", name=name, table=metric.table, text=text)
