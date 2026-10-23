"""describe_schema turns a SchemaInfo into prompt text. Pure function: no database needed."""

import logging

import pytest

from querymind.db.schema import (
    SCHEMA_TOKEN_BUDGET,
    Column,
    ForeignKey,
    SchemaInfo,
    Table,
    describe_schema,
    estimate_tokens,
    quote_name,
)

CUSTOMERS = Table(
    name="customers",
    columns=(
        Column(name="customer_id", data_type="character"),
        Column(name="company_name", data_type="character varying"),
    ),
    primary_key=("customer_id",),
    sample_rows=(("ALFKI", "Alfreds Futterkiste"),),
)

ORDERS = Table(
    name="orders",
    columns=(
        Column(name="order_id", data_type="smallint"),
        Column(name="customer_id", data_type="character"),
        Column(name="freight", data_type="real"),
        Column(name="order_date", data_type="date"),
    ),
    primary_key=("order_id",),
    foreign_keys=(
        ForeignKey(columns=("customer_id",), ref_table="customers", ref_columns=("customer_id",)),
    ),
    sample_rows=((10248, "VINET", 32.38, "1996-07-04"),),
)

SCHEMA = SchemaInfo(tables=(CUSTOMERS, ORDERS))


def one_table(*, columns: tuple[Column, ...], rows: tuple[tuple[object, ...], ...]) -> str:
    """Describe a single table called t with the given columns and sample rows."""
    table = Table.model_validate({"name": "t", "columns": columns, "sample_rows": rows})
    return describe_schema(SchemaInfo(tables=(table,)))


def test_full_output() -> None:
    assert describe_schema(SCHEMA) == (
        "Table customers\n"
        "  Columns: customer_id char, company_name varchar\n"
        "  Primary key: customer_id\n"
        "  Sample rows:\n"
        "    ('ALFKI', 'Alfreds Futterkiste')\n"
        "\n"
        "Table orders\n"
        "  Columns: order_id smallint, customer_id char, freight real, order_date date\n"
        "  Primary key: order_id\n"
        "  Foreign keys: customer_id -> customers(customer_id)\n"
        "  Sample rows:\n"
        "    (10248, 'VINET', 32.38, '1996-07-04')"
    )


def test_table_names_is_the_allowlist() -> None:
    assert SCHEMA.table_names == frozenset({"customers", "orders"})


def test_optional_lines_left_out() -> None:
    text = one_table(columns=(Column(name="a", data_type="integer"),), rows=())
    assert text == "Table t\n  Columns: a integer"


def test_composite_keys() -> None:
    table = Table(
        name="order_details",
        columns=(
            Column(name="order_id", data_type="smallint"),
            Column(name="product_id", data_type="smallint"),
        ),
        primary_key=("order_id", "product_id"),
        foreign_keys=(
            ForeignKey(columns=("order_id",), ref_table="orders", ref_columns=("order_id",)),
            ForeignKey(
                columns=("order_id", "product_id"),
                ref_table="lines",
                ref_columns=("o_id", "p_id"),
            ),
        ),
    )
    text = describe_schema(SchemaInfo(tables=(table,)))
    assert "  Primary key: order_id, product_id\n" in text
    assert (
        "  Foreign keys: order_id -> orders(order_id); "
        "order_id, product_id -> lines(o_id, p_id)" in text
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "NULL"),
        (True, "true"),
        (False, "false"),
        (0, "0"),
        (2.5, "2.5"),
        ("O'Brien", "'O''Brien'"),  # quotes escaped the SQL way
        ("line one\nline two", "'line one line two'"),  # stays on one line
    ],
)
def test_sample_values_formatted_like_sql(value: object, expected: str) -> None:
    text = one_table(columns=(Column(name="a", data_type="text"),), rows=((value,),))
    assert text.endswith(f"    ({expected})")


def test_long_text_is_truncated() -> None:
    text = one_table(columns=(Column(name="notes", data_type="text"),), rows=(("x" * 100,),))
    assert text.endswith("    ('" + "x" * 37 + "...')")


def test_binary_columns_are_never_shown() -> None:
    columns = (Column(name="id", data_type="integer"), Column(name="photo", data_type="bytea"))
    text = one_table(columns=columns, rows=((1, None),))
    assert text.endswith("    (1, <binary>)")


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2  # rounds up


def test_warns_when_over_budget(caplog: pytest.LogCaptureFixture) -> None:
    columns = tuple(
        Column(name=f"column_number_{i}", data_type="integer")
        for i in range(SCHEMA_TOKEN_BUDGET // 4)
    )
    with caplog.at_level(logging.WARNING):
        one_table(columns=columns, rows=())
    assert "schema description over budget" in caplog.text


def test_no_warning_within_budget(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        describe_schema(SCHEMA)
    assert caplog.text == ""


@pytest.mark.parametrize(
    ("name", "written"),
    [
        ("customer_id", "customer_id"),  # plain lowercase: bare, as before
        ("cdscode", "cdscode"),
        ("date", "date"),  # a keyword, but not a reserved one: works bare
        ("order", '"order"'),  # reserved: FROM order is a syntax error
        ("user", '"user"'),
        ("Free Meal Count (K-12)", '"Free Meal Count (K-12)"'),  # spaces, capitals, ( )
        ("aCL IgA", '"aCL IgA"'),  # bare, Postgres would fold it to lowercase
        ("T-CHO", '"T-CHO"'),
        ("2nd", '"2nd"'),  # starts with a digit
        ('say "hi"', '"say ""hi"""'),  # a quote inside is doubled
    ],
)
def test_quote_name(name: str, written: str) -> None:
    assert quote_name(name) == written


def test_names_that_need_quotes_are_quoted_everywhere() -> None:
    # BIRD's financial database: a table called "order", columns with capitals and spaces.
    order = Table(
        name="order",
        columns=(
            Column(name="order_id", data_type="bigint"),
            Column(name="Free Meal Count (K-12)", data_type="real"),
        ),
        primary_key=("order_id",),
        foreign_keys=(
            ForeignKey(columns=("Free Meal Count (K-12)",), ref_table="user", ref_columns=("Id",)),
        ),
    )
    assert describe_schema(SchemaInfo(tables=(order,))) == (
        'Table "order"\n'
        '  Columns: order_id bigint, "Free Meal Count (K-12)" real\n'
        "  Primary key: order_id\n"
        '  Foreign keys: "Free Meal Count (K-12)" -> "user"("Id")'
    )
