"""introspect_schema against the real Northwind database, connected as analyst_ro."""

from collections.abc import Iterator

import psycopg
import pytest

from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.schema import (
    SCHEMA_TOKEN_BUDGET,
    ForeignKey,
    SchemaInfo,
    Table,
    describe_schema,
    estimate_tokens,
    introspect_schema,
)

pytestmark = pytest.mark.integration

# The 14 tables the seed script grants analyst_ro SELECT on (data/seed_northwind.py).
GRANTED_TABLES = {
    "categories",
    "customer_customer_demo",
    "customer_demographics",
    "customers",
    "employee_territories",
    "employees",
    "order_details",
    "orders",
    "products",
    "region",
    "shippers",
    "suppliers",
    "territories",
    "us_states",
}


@pytest.fixture
def schema(ro_conn: psycopg.Connection) -> SchemaInfo:
    return introspect_schema(ro_conn)


def get_table(schema: SchemaInfo, name: str) -> Table:
    return next(t for t in schema.tables if t.name == name)


def test_sees_exactly_the_granted_tables(schema: SchemaInfo) -> None:
    assert schema.table_names == GRANTED_TABLES


def test_columns_in_table_order(schema: SchemaInfo) -> None:
    orders = get_table(schema, "orders")
    assert [c.name for c in orders.columns[:4]] == [
        "order_id",
        "customer_id",
        "employee_id",
        "order_date",
    ]
    assert orders.columns[3].data_type == "date"


def test_primary_keys(schema: SchemaInfo) -> None:
    assert get_table(schema, "orders").primary_key == ("order_id",)
    assert get_table(schema, "order_details").primary_key == ("order_id", "product_id")


def test_foreign_keys(schema: SchemaInfo) -> None:
    orders = get_table(schema, "orders")
    assert set(orders.foreign_keys) >= {
        ForeignKey(columns=("customer_id",), ref_table="customers", ref_columns=("customer_id",)),
        ForeignKey(columns=("employee_id",), ref_table="employees", ref_columns=("employee_id",)),
        ForeignKey(columns=("ship_via",), ref_table="shippers", ref_columns=("shipper_id",)),
    }


def test_sample_rows_ordered_by_primary_key(schema: SchemaInfo) -> None:
    orders = get_table(schema, "orders")
    assert len(orders.sample_rows) == 3
    assert [row[0] for row in orders.sample_rows] == [10248, 10249, 10250]
    # Dates arrive as ISO strings, ready for the prompt.
    assert orders.sample_rows[0][3] == "1996-07-04"


def test_binary_columns_not_read(schema: SchemaInfo) -> None:
    employees = get_table(schema, "employees")
    photo = next(i for i, c in enumerate(employees.columns) if c.name == "photo")
    assert employees.columns[photo].is_binary
    assert all(row[photo] is None for row in employees.sample_rows)


def test_description_fits_token_budget(schema: SchemaInfo) -> None:
    text = describe_schema(schema)
    assert estimate_tokens(text) <= SCHEMA_TOKEN_BUDGET
    assert "Table orders\n" in text
    assert "<binary>" in text  # employees.photo


@pytest.fixture
def hidden_table(settings: Settings) -> Iterator[str]:
    """A table analyst_ro has no grant on, pointing at employees (created and dropped as admin)."""
    # lock_timeout: if a lock is ever held by mistake, fail after 5 s instead of hanging.
    with psycopg.connect(
        settings.admin_conninfo(), autocommit=True, options="-c lock_timeout=5s"
    ) as admin:
        admin.execute(
            "CREATE TABLE zz_hidden_salaries"
            " (employee_id smallint REFERENCES employees, salary numeric)"
        )
        try:
            yield "zz_hidden_salaries"
        finally:
            admin.execute("DROP TABLE zz_hidden_salaries")


def test_tables_without_grant_stay_hidden(settings: Settings, hidden_table: str) -> None:
    # Own connection, closed right away: reading `employees` holds a lock until the connection
    # ends, and the fixture's DROP TABLE would wait on that lock forever.
    with connect_readonly(settings) as conn:
        schema = introspect_schema(conn)
    assert hidden_table not in schema.table_names
    assert hidden_table not in describe_schema(schema)  # not even through a foreign key
