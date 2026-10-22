"""Read a semantic layer YAML file, and check it against the real database schema.

    layer = load_semantic_layer(Path("semantic_layer/northwind.yaml"))   # shape errors
    check_against(layer, schema)                                          # name errors

Two separate checks: the file's *shape* (Pydantic, no database needed) and its *names*
(every table and column it mentions must exist). A definition that points at a column that
was renamed would teach the LLM to write SQL that fails, so both are errors, not warnings.
"""

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import ValidationError

from querymind.db.schema import SchemaInfo
from querymind.semantic.models import SemanticLayer


class SemanticLayerError(Exception):
    """The semantic layer file is missing, not valid YAML, badly shaped, or names unknown things."""


def load_semantic_layer(path: Path) -> SemanticLayer:
    """Load and validate one semantic layer file.

    Raises:
        SemanticLayerError: the file can't be read, isn't YAML, or doesn't match the models.
    """
    try:
        # safe_load, never yaml.load: plain YAML can contain tags that build Python objects.
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SemanticLayerError(f"Can't read the semantic layer {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SemanticLayerError(f"{path} is not valid YAML: {exc}") from exc
    try:
        return SemanticLayer.model_validate(raw)
    except ValidationError as exc:
        raise SemanticLayerError(f"{path} has the wrong shape:\n{exc}") from exc


def load_semantic_layers(paths: Sequence[Path]) -> list[SemanticLayer]:
    """Load several layers (e.g. Northwind + Chinook in one database). Names must not overlap."""
    layers = [load_semantic_layer(path) for path in paths]
    seen: dict[str, str] = {}
    for layer in layers:
        for name in {*layer.table_scope, *layer.metrics}:
            if name in seen:
                raise SemanticLayerError(
                    f"{name!r} is defined in both {seen[name]} and {layer.database}"
                )
            seen[name] = layer.database
    return layers


def check_against(layer: SemanticLayer, schema: SchemaInfo) -> None:
    """Every table and column the layer names must exist in `schema` (as the agent's role sees it).

    The SQL inside metrics is checked by running it (tests/integration/test_semantic_layer_db.py).

    Raises:
        SemanticLayerError: listing every unknown name at once, so one run shows all the fixes.
    """
    columns = {table.name: {c.name for c in table.columns} for table in schema.tables}
    problems: list[str] = []

    def check_column(table: str, column: str, where: str) -> None:
        if table not in columns:
            problems.append(f"{where}: unknown table {table}")
        elif column not in columns[table]:
            problems.append(f"{where}: unknown column {table}.{column}")

    for table in layer.scope:
        if table not in columns:
            problems.append(f"scope: unknown table {table}")
    for table, info in layer.tables.items():
        if table not in columns:
            problems.append(f"tables: unknown table {table}")
            continue
        for column in info.columns:
            check_column(table, column, f"tables.{table}")
    for name, metric in layer.metrics.items():
        if metric.table not in columns:
            problems.append(f"metrics.{name}: unknown table {metric.table}")
    for join in layer.joins:
        for ref in (join.left, join.right):
            table, column = ref.split(".")
            check_column(table, column, f"joins {join.left} = {join.right}")

    if problems:
        raise SemanticLayerError(
            f"The semantic layer for {layer.database} doesn't match the database:\n  "
            + "\n  ".join(problems)
        )
