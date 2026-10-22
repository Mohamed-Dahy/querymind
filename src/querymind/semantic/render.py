"""The semantic layer as prompt text: a short "Business definitions" block after the schema.

    text = schema_prompt_text(schema, layer)   # what the SQL prompt shows as the database

Only what helps write SQL goes in: metric definitions, other names for tables and columns,
column notes and preferred joins. PII flags are left out (they are for masking, Phase 5.4).
`tables=` keeps only the parts about those tables; schema retrieval (3.3) will use it.
"""

from collections.abc import Callable, Collection

from querymind.db.schema import SchemaInfo, describe_schema
from querymind.semantic.models import Metric, SemanticLayer

HEADER = (
    "Business definitions for this database. When the question uses one of these terms, "
    "use exactly this definition:"
)


def schema_prompt_text(schema: SchemaInfo, layer: SemanticLayer | None) -> str:
    """The schema description, followed by the business definitions (if there is a layer)."""
    text = describe_schema(schema)
    block = describe_semantics(layer) if layer is not None else ""
    return f"{text}\n\n{block}" if block else text


def describe_semantics(layer: SemanticLayer, tables: Collection[str] | None = None) -> str:
    """The layer as plain lines for the LLM. Empty when there is nothing to say."""

    def wanted(table: str) -> bool:
        return tables is None or table in tables

    sections = [
        _section(
            "Metrics and terms",
            [_metric(n, m) for n, m in layer.metrics.items() if wanted(m.table)],
        ),
        _section(
            "Other names for tables",
            [
                f"- {name}: {', '.join(info.synonyms)}"
                for name, info in layer.tables.items()
                if info.synonyms and wanted(name)
            ],
        ),
        _section(
            "Tables",
            [
                f"- {name}: {info.description}"
                for name, info in layer.tables.items()
                if info.description and wanted(name)
            ],
        ),
        _section("Columns", _column_lines(layer, wanted)),
        _section(
            "Preferred joins",
            [
                f"- {j.left} = {j.right}" + (f" ({j.note})" if j.note else "")
                for j in layer.joins
                if all(wanted(t) for t in j.tables)
            ],
        ),
    ]
    body = "\n\n".join(s for s in sections if s)
    return f"{HEADER}\n\n{body}" if body else ""


def _metric(name: str, metric: Metric) -> str:
    also = f" (also: {', '.join(metric.synonyms)})" if metric.synonyms else ""
    return (
        f"- {name}{also}: {metric.kind} on {metric.table}. {metric.description}\n"
        f"    SQL: {' '.join(metric.sql.split())}"  # one line, however the YAML wrapped it
    )


def _column_lines(layer: SemanticLayer, wanted: Callable[[str], bool]) -> list[str]:
    lines = []
    for table, info in layer.tables.items():
        if not wanted(table):
            continue
        for column, column_info in info.columns.items():
            parts = [column_info.description.rstrip(".")] if column_info.description else []
            if column_info.synonyms:
                parts.append(f"also called {', '.join(column_info.synonyms)}")
            if parts:
                lines.append(f"- {table}.{column}: {'; '.join(parts)}")
    return lines


def _section(title: str, lines: list[str]) -> str:
    return f"{title}:\n" + "\n".join(lines) if lines else ""
