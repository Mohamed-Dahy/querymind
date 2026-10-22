"""The semantic layer: models, loader errors, name checks and prompt text. No database."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from querymind.agent.pipeline import Pipeline
from querymind.config import Settings
from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table, describe_schema
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.loader import SemanticLayerError, check_against, load_semantic_layer
from querymind.semantic.models import SemanticLayer
from querymind.semantic.render import HEADER, describe_semantics, schema_prompt_text

NORTHWIND = Path(__file__).parents[2] / "semantic_layer" / "northwind.yaml"

SCHEMA = SchemaInfo(
    tables=(
        Table(
            name="customers",
            columns=(
                Column(name="customer_id", data_type="varchar"),
                Column(name="phone", data_type="varchar"),
            ),
        ),
        Table(
            name="orders",
            columns=(
                Column(name="order_id", data_type="smallint"),
                Column(name="customer_id", data_type="varchar"),
            ),
            foreign_keys=(
                ForeignKey(
                    columns=("customer_id",), ref_table="customers", ref_columns=("customer_id",)
                ),
            ),
        ),
    )
)
LAYER = SemanticLayer.model_validate(
    {
        "database": "shop",
        "version": 1,
        "tables": {
            "customers": {
                "description": "People who buy.",
                "synonyms": ["clients"],
                "columns": {"phone": {"pii": True}},
            },
            "orders": {"columns": {"customer_id": {"description": "Who ordered."}}},
        },
        "metrics": {
            "order_count": {
                "kind": "measure",
                "table": "orders",
                "description": "Number of orders.",
                "synonyms": ["volume"],
                "sql": "count(\n  orders.order_id)",
            },
            "vip": {
                "kind": "condition",
                "table": "customers",
                "description": "A big customer.",
                "sql": "customers.customer_id = 'X'",
            },
        },
        "joins": [{"left": "orders.customer_id", "right": "customers.customer_id", "note": "who"}],
    }
)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "layer.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# --- The shipped file -------------------------------------------------------------------------


def test_the_northwind_layer_loads() -> None:
    layer = load_semantic_layer(NORTHWIND)
    assert layer.database == "northwind"
    assert {"revenue", "key_account", "active_customer", "late_shipment"} <= set(layer.metrics)
    assert ("customers", "phone") in layer.pii_columns  # personal data is declared (5.4 masks it)


# --- Loader errors ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("database: x\nversion: 1\ntables:\n  customers:\n    synonym: [a]\n", "synonym"),
        ("database: x\nversion: 1\ntables:\n  Customers: {}\n", "lowercase SQL names"),
        (
            "database: x\nversion: 1\nmetrics:\n  m: {kind: total, table: t, sql: x, "
            "description: d}\n",
            "kind",
        ),
        ("database: x\nversion: 1\njoins:\n  - {left: orders, right: customers.id}\n", "left"),
        ("database: x\nversion: 1\ntables: [1, 2\n", "not valid YAML"),
    ],
)
def test_broken_files_are_rejected_with_the_reason(tmp_path: Path, text: str, problem: str) -> None:
    with pytest.raises(SemanticLayerError, match=problem):
        load_semantic_layer(write(tmp_path, text))


def test_a_missing_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(SemanticLayerError, match="Can't read"):
        load_semantic_layer(tmp_path / "nope.yaml")


# --- Names must exist in the database ---------------------------------------------------------


def test_a_matching_layer_passes() -> None:
    check_against(LAYER, SCHEMA)


def test_every_unknown_name_is_listed_at_once() -> None:
    broken = SemanticLayer.model_validate(
        {
            "database": "shop",
            "version": 1,
            "tables": {"clients": {}, "customers": {"columns": {"email": {}}}},
            "metrics": {
                "m": {"kind": "measure", "table": "invoices", "sql": "count(*)", "description": "d"}
            },
            "joins": [{"left": "orders.buyer_id", "right": "customers.customer_id"}],
        }
    )
    with pytest.raises(SemanticLayerError) as info:
        check_against(broken, SCHEMA)
    message = str(info.value)
    for problem in [
        "tables: unknown table clients",
        "unknown column customers.email",
        "metrics.m: unknown table invoices",
        "unknown column orders.buyer_id",
    ]:
        assert problem in message


# --- Prompt text ------------------------------------------------------------------------------


def test_the_block_shows_definitions_names_notes_and_joins() -> None:
    text = describe_semantics(LAYER)
    assert text.startswith(HEADER)
    assert "- order_count (also: volume): measure on orders. Number of orders." in text
    assert "    SQL: count( orders.order_id)" in text  # one line, however the YAML wrapped it
    assert "- customers: clients" in text
    assert "- orders.customer_id: Who ordered" in text
    assert "- orders.customer_id = customers.customer_id (who)" in text
    assert "pii" not in text.lower() and "phone" not in text  # PII flags are not prompt text


def test_the_block_can_be_cut_to_some_tables() -> None:
    text = describe_semantics(LAYER, tables={"customers"})
    assert "vip" in text and "clients" in text
    assert "order_count" not in text  # a metric on orders
    assert "Preferred joins" not in text  # the join needs both tables


def test_no_layer_means_the_plain_schema_text() -> None:
    assert schema_prompt_text(SCHEMA, None) == describe_schema(SCHEMA)
    empty = SemanticLayer(database="shop", version=1)
    assert schema_prompt_text(SCHEMA, empty) == describe_schema(SCHEMA)


def test_the_schema_is_followed_by_the_block() -> None:
    text = schema_prompt_text(SCHEMA, LAYER)
    assert text == f"{describe_schema(SCHEMA)}\n\n{describe_semantics(LAYER)}"


# --- Wiring -----------------------------------------------------------------------------------


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "postgres_host": "127.0.0.1",
        "postgres_port": 5433,
        "postgres_db": "northwind",
        "postgres_admin_user": "admin",
        "postgres_admin_password": SecretStr("x"),
        "analyst_ro_user": "analyst_ro",
        "analyst_ro_password": SecretStr("x"),
        "llm_provider": "groq",
        "groq_api_key": SecretStr("gsk_test"),
        "groq_model": "openai/gpt-oss-120b",
    }
    return Settings.model_validate(values | overrides)


def test_the_default_setting_points_at_the_northwind_layer() -> None:
    assert settings().semantic_layer_path == Path("semantic_layer/northwind.yaml")
    assert settings(semantic_layer_path="").semantic_layer_path is None  # empty = no layer


def test_a_broken_layer_fails_when_the_app_starts(tmp_path: Path) -> None:
    broken = write(tmp_path, "database: x\n")  # no version
    with pytest.raises(SemanticLayerError, match="version"):
        Pipeline(llm=FakeLLMProvider(), settings=settings(semantic_layer_path=broken))
