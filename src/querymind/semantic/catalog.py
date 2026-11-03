"""What the data explorer shows: the tables and what they mean, and never a value from them.

    catalog = build_catalog(schema, row_counts, layers)     # the structure: tables, columns, keys
    overview = build_overview(layers, dictionaries, examples)  # the meaning: terms, notes, joins

Pure functions over what is already loaded, so no database is needed to test them. The models below
have no field that could hold a row, on purpose: a sample row stays a prompt-only detail, and PII
masking doesn't exist yet (5.4).
"""

import logging
from collections.abc import Collection, Mapping, Sequence
from typing import Literal

from pydantic import BaseModel

from querymind.agent.schemas import FewShotExample
from querymind.db.schema import SchemaInfo, Table
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.models import SemanticLayer, TableInfo

logger = logging.getLogger(__name__)

MAX_SUGGESTED_QUESTIONS = 5


class CatalogColumn(BaseModel):
    name: str
    data_type: str


class CatalogForeignKey(BaseModel):
    """`columns` of this table point to `ref_columns` of `ref_table` (same order)."""

    columns: list[str]
    ref_table: str
    ref_columns: list[str]


class CatalogTable(BaseModel):
    name: str
    business: str | None  # the semantic layer that covers it; None if none does
    row_count: int | None  # Postgres's estimate, or a count; None if neither could be had
    columns: list[CatalogColumn]
    primary_key: list[str]
    foreign_keys: list[CatalogForeignKey]


class Catalog(BaseModel):
    tables: list[CatalogTable]


class ColumnMeaning(BaseModel):
    name: str
    description: str  # written by a person, in the semantic layer; "" if there is none
    synonyms: list[str]
    pii: bool
    dictionary_note: str  # from the data dictionary; "" if there is none


class TableMeaning(BaseModel):
    name: str
    description: str  # written by a person, in the semantic layer; "" if there is none
    synonyms: list[str]
    dictionary_description: str  # from the data dictionary; "" if there is none
    dictionary_reviewed: bool  # False: an LLM's draft that nobody has checked yet
    columns: list[ColumnMeaning]
    suggested_questions: list[str]  # verified example questions; their SQL stays out


class MetricMeaning(BaseModel):
    name: str
    kind: Literal["measure", "condition"]
    table: str
    sql: str
    description: str
    synonyms: list[str]


class JoinMeaning(BaseModel):
    left: str
    right: str
    note: str


class BusinessMeaning(BaseModel):
    database: str
    description: str
    tables: list[TableMeaning]
    metrics: list[MetricMeaning]
    joins: list[JoinMeaning]


class SemanticOverview(BaseModel):
    businesses: list[BusinessMeaning]


def build_catalog(
    schema: SchemaInfo,
    row_counts: Mapping[str, int | None],
    layers: Sequence[SemanticLayer],
) -> Catalog:
    """The structure of every table. `schema`'s sample rows are not copied over."""
    business = {table: layer.database for layer in layers for table in layer.table_scope}
    return Catalog(
        tables=[_catalog_table(table, business, row_counts) for table in schema.tables],
    )


def _catalog_table(
    table: Table, business: Mapping[str, str], row_counts: Mapping[str, int | None]
) -> CatalogTable:
    return CatalogTable(
        name=table.name,
        business=business.get(table.name),
        row_count=row_counts.get(table.name),
        columns=[CatalogColumn(name=c.name, data_type=c.data_type) for c in table.columns],
        primary_key=list(table.primary_key),
        foreign_keys=[
            CatalogForeignKey(
                columns=list(fk.columns), ref_table=fk.ref_table, ref_columns=list(fk.ref_columns)
            )
            for fk in table.foreign_keys
        ],
    )


def build_overview(
    layers: Sequence[SemanticLayer],
    dictionaries: Mapping[str, DataDictionary],
    examples: Sequence[FewShotExample],
) -> SemanticOverview:
    """What each business's tables and terms mean: the written layer, then the dictionary."""
    known = {table for layer in layers for table in layer.table_scope}
    questions = _questions_per_table(examples, known)
    return SemanticOverview(
        businesses=[
            _business(layer, dictionaries.get(layer.database), questions) for layer in layers
        ]
    )


def _business(
    layer: SemanticLayer,
    dictionary: DataDictionary | None,
    questions: Mapping[str, list[str]],
) -> BusinessMeaning:
    drafts = dictionary.tables if dictionary else {}
    return BusinessMeaning(
        database=layer.database,
        description=layer.description,
        tables=[
            _table(name, layer.tables.get(name), drafts.get(name), questions.get(name, []))
            for name in layer.table_scope
        ],
        metrics=[
            MetricMeaning(
                name=name,
                kind=metric.kind,
                table=metric.table,
                sql=metric.sql,
                description=metric.description,
                synonyms=list(metric.synonyms),
            )
            for name, metric in layer.metrics.items()
        ],
        joins=[JoinMeaning(left=j.left, right=j.right, note=j.note) for j in layer.joins],
    )


def _table(
    name: str,
    info: TableInfo | None,
    draft: DictionaryTable | None,
    questions: list[str],
) -> TableMeaning:
    written = info.columns if info else {}
    drafted = draft.columns if draft else {}
    return TableMeaning(
        name=name,
        description=info.description if info else "",
        synonyms=list(info.synonyms) if info else [],
        dictionary_description=draft.description if draft else "",
        dictionary_reviewed=draft.reviewed if draft else False,
        # Either source can name a column the other doesn't. Dictionary first: it lists them all,
        # in the table's order.
        columns=[
            ColumnMeaning(
                name=column,
                description=written[column].description if column in written else "",
                synonyms=list(written[column].synonyms) if column in written else [],
                pii=written[column].pii if column in written else False,
                dictionary_note=drafted.get(column, ""),
            )
            for column in dict.fromkeys([*drafted, *written])
        ],
        suggested_questions=questions,
    )


def _questions_per_table(
    examples: Sequence[FewShotExample], known: Collection[str]
) -> dict[str, list[str]]:
    """Each table's example questions, those about the fewest tables first: they say most about it.

    The validator finds the tables, so a CTE's name doesn't count as one. An example it refuses
    can't be matched; its own tests (test_examples_db.py) are what catch that, so it is only logged.
    """
    used: list[tuple[str, frozenset[str]]] = []
    for example in examples:
        try:
            tables = validate_sql(example.sql, allowed_tables=known, max_rows=1).tables
        except UnsafeSqlError:
            logger.warning("example not matched to tables", extra={"example": example.id})
            continue
        used.append((example.question, tables))

    questions: dict[str, list[str]] = {}
    for question, tables in sorted(used, key=lambda pair: len(pair[1])):  # stable: file order stays
        for table in tables:
            found = questions.setdefault(table, [])
            if len(found) < MAX_SUGGESTED_QUESTIONS:
                found.append(question)
    return questions
