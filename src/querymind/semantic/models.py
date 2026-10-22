"""The semantic layer: what the company's words mean in this database (Phase 3.1).

    layer = load_semantic_layer(Path("semantic_layer/northwind.yaml"))
    layer.metrics["revenue"].sql   # "sum(order_details.unit_price * ...)"

A database stores *data*; the semantic layer stores *meaning*: which table "clients" are, how
"revenue" is computed, what counts as a "key account". The LLM can't guess a company's own
definitions, so they are written down once (YAML, reviewed by a person) and shown in the prompt.

Everything is plain data, validated by Pydantic. `extra="forbid"` everywhere: a misspelt key
("synonym:") is an error, not silently ignored.
"""

import re
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Lowercase SQL names, like Postgres folds unquoted names: customers, order_details, ...
NAME = r"^[a-z_][a-z0-9_]*$"
COLUMN_REF = r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$"  # table.column


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ColumnInfo(_Model):
    description: str = ""
    synonyms: tuple[str, ...] = ()  # other words for this column, e.g. "carrier" for ship_via
    # Personal data. Declared here; masked in results from Phase 5.4.
    pii: bool = False


class TableInfo(_Model):
    description: str = ""
    synonyms: tuple[str, ...] = ()  # other words for this table, e.g. "clients" for customers
    columns: dict[str, ColumnInfo] = {}

    @field_validator("columns")
    @classmethod
    def _column_names(cls, columns: dict[str, ColumnInfo]) -> dict[str, ColumnInfo]:
        _check_names(columns, "column")
        return columns


class Metric(_Model):
    """A business term with an exact SQL definition.

    - `measure`: an aggregate expression, e.g. revenue = sum(...). Evaluated over `table`.
    - `condition`: a true/false test on rows of `table`, e.g. a late shipment, a key account.
    """

    kind: Literal["measure", "condition"]
    table: str = Field(pattern=NAME)  # the table the expression is written against
    sql: str = Field(min_length=1)
    description: str = Field(min_length=1)
    synonyms: tuple[str, ...] = ()


class JoinPath(_Model):
    """A preferred way to join two tables, e.g. orders.ship_via = shippers.shipper_id."""

    left: str = Field(pattern=COLUMN_REF)
    right: str = Field(pattern=COLUMN_REF)
    note: str = ""

    @property
    def tables(self) -> tuple[str, str]:
        return self.left.split(".")[0], self.right.split(".")[0]


class SemanticLayer(_Model):
    database: str
    version: int
    # What this part of the database is about. Several layers can share one database (3.2).
    description: str = ""
    # Every table this layer covers, described or not (e.g. Northwind's `region`). Empty = just
    # the tables listed under `tables`. The data dictionary (3.2) and retrieval (3.3) use it.
    scope: tuple[str, ...] = ()
    tables: dict[str, TableInfo] = {}
    metrics: dict[str, Metric] = {}
    joins: tuple[JoinPath, ...] = ()

    @model_validator(mode="after")
    def _names_are_valid(self) -> Self:
        _check_names(self.tables, "table")
        _check_names(self.metrics, "metric")
        if self.scope:
            outside = sorted(set(self.tables) - set(self.scope))
            if outside:
                raise ValueError(f"tables described but not in scope: {', '.join(outside)}")
        return self

    @property
    def table_scope(self) -> tuple[str, ...]:
        """The tables this layer covers."""
        return self.scope or tuple(self.tables)

    @property
    def pii_columns(self) -> frozenset[tuple[str, str]]:
        """(table, column) pairs marked as personal data."""
        return frozenset(
            (table, column)
            for table, info in self.tables.items()
            for column, column_info in info.columns.items()
            if column_info.pii
        )


def _check_names(names: Mapping[str, object], what: str) -> None:
    bad = sorted(name for name in names if not re.fullmatch(NAME, name))
    if bad:
        raise ValueError(f"{what} names must be lowercase SQL names: {', '.join(bad)}")
