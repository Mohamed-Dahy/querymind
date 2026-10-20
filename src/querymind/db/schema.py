"""Read the database structure and turn it into compact text the LLM can read in a prompt.

Two parts, kept separate so the formatting can be unit-tested without a database:
  - `introspect_schema(conn)`: asks Postgres for tables, columns, keys and a few sample rows.
  - `describe_schema(schema)`: pure function, turns that into text like:

        Table orders
          Columns: order_id smallint, customer_id char, order_date date, ...
          Primary key: order_id
          Foreign keys: customer_id -> customers(customer_id)
          Sample rows:
            (10248, 'VINET', '1996-07-04', ...)

Run it as the read-only role: Postgres then only lists the tables that role may read, and that
same list is what the SQL validator (Phase 1.3) will allow.
"""

import datetime as dt
import logging
import math
from collections.abc import Collection, Sequence
from decimal import Decimal

import psycopg
from psycopg import sql
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

# The schema text should fit comfortably in a prompt next to the question and instructions.
SCHEMA_TOKEN_BUDGET = 4000
# Long text values (addresses, notes) are cut to this many characters in sample rows.
MAX_SAMPLE_CHARS = 40
# Column types whose values are never sampled: raw bytes mean nothing to the LLM (photos).
BINARY_TYPES = frozenset({"bytea"})

# information_schema type names → shorter names the LLM knows just as well (fewer tokens).
_SHORT_TYPES = {
    "character varying": "varchar",
    "character": "char",
    "double precision": "double",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "time without time zone": "time",
}

# A sample value after conversion: only plain types, so the models stay simple.
type SampleValue = str | int | float | bool | None


class Column(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    data_type: str

    @property
    def is_binary(self) -> bool:
        return self.data_type in BINARY_TYPES


class ForeignKey(BaseModel):
    """`columns` of this table point to `ref_columns` of `ref_table` (same order)."""

    model_config = ConfigDict(frozen=True)

    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]


class Table(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    # Each row has one value per column, in column order. Binary columns hold None.
    sample_rows: tuple[tuple[SampleValue, ...], ...] = ()


class SchemaInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    tables: tuple[Table, ...]

    @property
    def table_names(self) -> frozenset[str]:
        """Names of every table the role can read (the validator's table allowlist)."""
        return frozenset(table.name for table in self.tables)


# --- Reading from the database -----------------------------------------------------------

# Only lists columns of tables the current role has a privilege on.
_COLUMNS_QUERY = """
SELECT c.table_name::text, c.column_name::text, c.data_type::text
FROM information_schema.columns AS c
JOIN information_schema.tables AS t USING (table_schema, table_name)
WHERE c.table_schema = %(schema)s AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position
"""

# Keys come from pg_catalog, not information_schema: information_schema hides constraints of
# tables the role can only SELECT from, so analyst_ro would see no keys at all.
# unnest(...) WITH ORDINALITY keeps the key columns in their declared order.
_KEYS_QUERY = """
SELECT
    con.contype::text,
    rel.relname::text,
    ref.relname::text,
    ARRAY(
        SELECT att.attname::text
        FROM unnest(con.conkey) WITH ORDINALITY AS k(attnum, pos)
        JOIN pg_attribute AS att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
        ORDER BY k.pos
    ),
    ARRAY(
        SELECT att.attname::text
        FROM unnest(con.confkey) WITH ORDINALITY AS k(attnum, pos)
        JOIN pg_attribute AS att ON att.attrelid = con.confrelid AND att.attnum = k.attnum
        ORDER BY k.pos
    )
FROM pg_constraint AS con
JOIN pg_class AS rel ON rel.oid = con.conrelid
JOIN pg_namespace AS ns ON ns.oid = rel.relnamespace
LEFT JOIN pg_class AS ref ON ref.oid = con.confrelid
WHERE ns.nspname = %(schema)s AND con.contype IN ('p', 'f')
ORDER BY rel.relname, con.conname
"""


def introspect_schema(
    conn: psycopg.Connection, *, schema: str = "public", sample_rows: int = 3
) -> SchemaInfo:
    """Read every table the connected role can see in `schema`, with keys and sample rows."""
    columns = _read_columns(conn, schema)
    primary_keys, foreign_keys = _read_keys(conn, schema, visible=columns.keys())

    tables = []
    for name, table_columns in columns.items():
        primary_key = primary_keys.get(name, ())
        samples = _read_samples(
            conn, schema, name, table_columns, order_by=primary_key, limit=sample_rows
        )
        tables.append(
            Table(
                name=name,
                columns=tuple(table_columns),
                primary_key=primary_key,
                foreign_keys=tuple(foreign_keys.get(name, [])),
                sample_rows=samples,
            )
        )
    return SchemaInfo(tables=tuple(tables))


def _read_columns(conn: psycopg.Connection, schema: str) -> dict[str, list[Column]]:
    columns: dict[str, list[Column]] = {}
    for table, column, data_type in conn.execute(_COLUMNS_QUERY, {"schema": schema}):
        columns.setdefault(table, []).append(Column(name=column, data_type=data_type))
    return columns


def _read_keys(
    conn: psycopg.Connection, schema: str, *, visible: Collection[str]
) -> tuple[dict[str, tuple[str, ...]], dict[str, list[ForeignKey]]]:
    """Primary and foreign keys, keeping only tables in `visible`.

    pg_catalog lists constraints of every table, including ones this role can't read, so we
    filter: a key is kept only if its table (and, for a foreign key, the table it points to)
    is visible. That way the LLM never learns about tables it isn't allowed to query.
    """
    primary_keys: dict[str, tuple[str, ...]] = {}
    foreign_keys: dict[str, list[ForeignKey]] = {}
    for kind, table, ref_table, cols, ref_cols in conn.execute(_KEYS_QUERY, {"schema": schema}):
        if table not in visible:
            continue
        if kind == "p":
            primary_keys[table] = tuple(cols)
        elif ref_table in visible:
            foreign_keys.setdefault(table, []).append(
                ForeignKey(columns=tuple(cols), ref_table=ref_table, ref_columns=tuple(ref_cols))
            )
    return primary_keys, foreign_keys


def _read_samples(
    conn: psycopg.Connection,
    schema: str,
    table: str,
    columns: Sequence[Column],
    *,
    order_by: Sequence[str],
    limit: int,
) -> tuple[tuple[SampleValue, ...], ...]:
    """First `limit` rows of the table (by primary key, so the output is stable).

    Binary columns are replaced by NULL in the query itself, so their bytes are never read.
    Table and column names go through sql.Identifier: they are quoted safely, never pasted
    into the SQL as raw text.
    """
    select_list = sql.SQL(", ").join(
        sql.SQL("NULL") if column.is_binary else sql.Identifier(column.name) for column in columns
    )
    query = sql.SQL("SELECT {} FROM {}").format(select_list, sql.Identifier(schema, table))
    if order_by:
        query += sql.SQL(" ORDER BY {}").format(
            sql.SQL(", ").join(sql.Identifier(name) for name in order_by)
        )
    query += sql.SQL(" LIMIT {}").format(sql.Literal(limit))
    return tuple(tuple(_to_sample(value) for value in row) for row in conn.execute(query))


def _to_sample(value: object) -> SampleValue:
    """Turn a database value into a plain type (dates become ISO strings, Decimal a float)."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.time):  # dt.datetime is a dt.date too
        return value.isoformat()
    return str(value)


# --- Formatting for the prompt -----------------------------------------------------------


def describe_schema(schema: SchemaInfo) -> str:
    """Compact text description of every table, for the SQL-generation prompt."""
    text = "\n\n".join(_describe_table(table) for table in schema.tables)
    tokens = estimate_tokens(text)
    if tokens > SCHEMA_TOKEN_BUDGET:
        # Still returned: the prompt may just be expensive. Big schemas get RAG in Phase 3.
        logger.warning(
            "schema description over budget",
            extra={"tokens": tokens, "budget": SCHEMA_TOKEN_BUDGET},
        )
    return text


def estimate_tokens(text: str) -> int:
    """Rough token count: about 4 characters per token for English text and SQL.

    Good enough for a budget check, and needs no tokenizer library.
    """
    return math.ceil(len(text) / 4)


def _describe_table(table: Table) -> str:
    columns = ", ".join(
        f"{c.name} {_SHORT_TYPES.get(c.data_type, c.data_type)}" for c in table.columns
    )
    lines = [f"Table {table.name}", f"  Columns: {columns}"]
    if table.primary_key:
        lines.append(f"  Primary key: {', '.join(table.primary_key)}")
    if table.foreign_keys:
        keys = "; ".join(_describe_foreign_key(fk) for fk in table.foreign_keys)
        lines.append(f"  Foreign keys: {keys}")
    if table.sample_rows:
        lines.append("  Sample rows:")
        for row in table.sample_rows:
            values = (
                "<binary>" if column.is_binary else _format_value(value)
                for column, value in zip(table.columns, row, strict=True)
            )
            lines.append(f"    ({', '.join(values)})")
    return "\n".join(lines)


def _describe_foreign_key(fk: ForeignKey) -> str:
    return f"{', '.join(fk.columns)} -> {fk.ref_table}({', '.join(fk.ref_columns)})"


def _format_value(value: SampleValue) -> str:
    """Write a value the way it would look in SQL: 'text' quoted, numbers bare, NULL."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):  # before int: True is an int in Python
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    # One line per row: newlines inside values (e.g. employee notes) become spaces.
    value = " ".join(value.split())
    if len(value) > MAX_SAMPLE_CHARS:
        value = value[: MAX_SAMPLE_CHARS - 3] + "..."
    return "'" + value.replace("'", "''") + "'"
