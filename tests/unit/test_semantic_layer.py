"""The semantic layer: models, loader errors, name checks and prompt text. No database."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from querymind.agent.pipeline import Pipeline
from querymind.config import Settings
from querymind.db.schema import Column, ForeignKey, SchemaInfo, Table, describe_schema
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.loader import (
    SemanticLayerError,
    check_against,
    load_semantic_layer,
    load_semantic_layers,
)
from querymind.semantic.models import SemanticLayer
from querymind.semantic.render import HEADER, describe_semantics, schema_prompt_text

LAYERS_DIR = Path(__file__).parents[2] / "semantic_layer"
NORTHWIND = LAYERS_DIR / "northwind.yaml"
CHINOOK = LAYERS_DIR / "chinook.yaml"

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
        "description": "Our shop.",
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


def test_northwind_and_chinook_load_together() -> None:
    northwind, chinook = load_semantic_layers([NORTHWIND, CHINOOK])
    assert chinook.database == "chinook"
    assert {"music_sales", "big_spender", "long_track"} <= set(chinook.metrics)
    assert ("customer", "email") in chinook.pii_columns
    # One database, two businesses: the singular/plural tables don't collide.
    assert "customer" in chinook.tables and "customers" in northwind.tables


def test_scope_lists_every_table_a_layer_covers() -> None:
    northwind = load_semantic_layer(NORTHWIND)
    assert "region" in northwind.table_scope  # covered, though it has no description
    assert LAYER.table_scope == ("customers", "orders")  # no scope: the described tables
    with pytest.raises(ValueError, match="not in scope: orders"):
        SemanticLayer.model_validate(
            {"database": "x", "version": 1, "scope": ["customers"], "tables": {"orders": {}}}
        )


def test_two_layers_may_not_define_the_same_name(tmp_path: Path) -> None:
    a = write(tmp_path, "database: a\nversion: 1\ntables:\n  orders: {}\n")
    b = tmp_path / "b.yaml"
    b.write_text("database: b\nversion: 1\ntables:\n  orders: {}\n", encoding="utf-8")
    with pytest.raises(SemanticLayerError, match="'orders' is defined in both a and b"):
        load_semantic_layers([a, b])


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
            "scope": ["clients", "customers"],
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
        "scope: unknown table clients",
        "tables: unknown table clients",
        "unknown column customers.email",
        "metrics.m: unknown table invoices",
        "unknown column orders.buyer_id",
    ]:
        assert problem in message


# --- Prompt text ------------------------------------------------------------------------------


def test_the_block_shows_definitions_names_notes_and_joins() -> None:
    text = describe_semantics(LAYER)
    assert text.startswith(f"Business definitions for the shop tables: Our shop. {HEADER}")
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
    assert schema_prompt_text(SCHEMA, []) == describe_schema(SCHEMA)
    empty = SemanticLayer(database="shop", version=1)
    assert schema_prompt_text(SCHEMA, [empty]) == describe_schema(SCHEMA)


def test_the_schema_is_followed_by_the_block() -> None:
    other = SemanticLayer.model_validate(
        {"database": "music", "version": 1, "tables": {"track": {"synonyms": ["songs"]}}}
    )
    text = schema_prompt_text(SCHEMA, [LAYER, other])
    assert text == "\n\n".join(
        [describe_schema(SCHEMA), describe_semantics(LAYER), describe_semantics(other)]
    )


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


def test_the_default_setting_points_at_both_layers() -> None:
    assert settings().semantic_layer_paths == [
        Path("semantic_layer/northwind.yaml"),
        Path("semantic_layer/chinook.yaml"),
    ]


def test_the_env_value_is_comma_separated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMANTIC_LAYER_PATHS", "a.yaml, b.yaml")
    assert settings().semantic_layer_paths == [Path("a.yaml"), Path("b.yaml")]
    monkeypatch.setenv("SEMANTIC_LAYER_PATHS", "")
    assert settings().semantic_layer_paths == []  # empty = no definitions


def test_a_broken_layer_fails_when_the_app_starts(tmp_path: Path) -> None:
    broken = write(tmp_path, "database: x\n")  # no version
    with pytest.raises(SemanticLayerError, match="version"):
        Pipeline(llm=FakeLLMProvider(), settings=settings(semantic_layer_paths=[broken]))
