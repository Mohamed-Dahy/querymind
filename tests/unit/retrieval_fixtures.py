"""A small two-business schema for the retrieval tests: food trading (Northwind-like) and a
music store (Chinook-like), joined by foreign keys within each business, never across."""

from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.models import SemanticLayer


def table(name: str, *columns: str, fks: tuple[ForeignKey, ...] = ()) -> Table:
    return Table(
        name=name,
        columns=tuple(Column(name=c, data_type="text") for c in columns),
        primary_key=(columns[0],),
        foreign_keys=fks,
    )


def fk(column: str, ref_table: str, ref_column: str) -> ForeignKey:
    return ForeignKey(columns=(column,), ref_table=ref_table, ref_columns=(ref_column,))


SCHEMA = SchemaInfo(
    tables=(
        table("customers", "customer_id", "company_name", "country"),
        table(
            "orders",
            "order_id",
            "customer_id",
            "ship_via",
            fks=(
                fk("customer_id", "customers", "customer_id"),
                fk("ship_via", "shippers", "shipper_id"),
            ),
        ),
        table(
            "order_details",
            "order_id",
            "product_id",
            "quantity",
            fks=(fk("order_id", "orders", "order_id"), fk("product_id", "products", "product_id")),
        ),
        table("products", "product_id", "product_name"),
        table("shippers", "shipper_id", "company_name"),
        table("customer", "customer_id", "first_name", "email"),
        table(
            "invoice",
            "invoice_id",
            "customer_id",
            "total",
            fks=(fk("customer_id", "customer", "customer_id"),),
        ),
        table("audit_notes", "note_id", "body"),  # in no layer: still gets a document
    )
)

FOOD = SemanticLayer.model_validate(
    {
        "database": "northwind",
        "version": 1,
        "description": "Our food trading business.",
        "scope": ["customers", "orders", "order_details", "products", "shippers"],
        "tables": {
            "customers": {
                "description": "Companies that buy our food products.",
                "synonyms": ["clients"],
                "columns": {
                    "company_name": {"description": "Legal name.", "synonyms": ["client name"]}
                },
            },
            "orders": {"description": "One row per food order."},
        },
        "metrics": {
            "revenue": {
                "kind": "measure",
                "table": "order_details",
                "sql": "sum(order_details.quantity)",
                "description": "Money from food sales.",
                "synonyms": ["turnover"],
            }
        },
        "joins": [{"left": "orders.customer_id", "right": "customers.customer_id"}],
    }
)

MUSIC = SemanticLayer.model_validate(
    {
        "database": "chinook",
        "version": 1,
        "description": "Our online music store.",
        "scope": ["customer", "invoice"],
        "tables": {"customer": {"columns": {"email": {"pii": True}}}},
        "metrics": {
            "big_spender": {
                "kind": "condition",
                "table": "customer",
                "sql": "customer.customer_id IN (SELECT customer_id FROM invoice)",
                "description": "A music customer who spent at least 45.",
            }
        },
    }
)

DICTIONARIES = {
    "northwind": DataDictionary(
        database="northwind",
        tables={
            # The layer's own description of customers wins over this draft.
            "customers": DictionaryTable(description="LLM draft: customer companies."),
            "shippers": DictionaryTable(
                description="Delivery companies that ship orders.",
                columns={"company_name": "Name of the delivery company."},
            ),
        },
    ),
    "chinook": DataDictionary(
        database="chinook",
        tables={"customer": DictionaryTable(description="People who buy music tracks.")},
    ),
}
