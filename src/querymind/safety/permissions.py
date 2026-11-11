"""Table permissions (5.2): the part of a database a role may see, and nothing about the rest.

    allowed = frozenset({"orders", "products"})        # from store/table_access.py; None = all
    visible_schema(schema, allowed)                    # only those tables, no key pointing out
    visible_layers(layers, allowed)                    # their descriptions, terms and joins
    visible_examples(examples, allowed)                # few-shot / suggested questions on them

The rule is stored per workspace, database and role (store/table_access.py); here it is applied.
It is enforced twice: the validator's allowlist refuses SQL on any other table, and everything the
LLM or the explorer shows is built from these views, so a hidden table is never even named:
- a foreign key pointing at a hidden table is dropped (it would name it);
- a business term whose SQL reads a hidden table is dropped (the SQL would name it);
- a preferred join touching a hidden table, and an example reading one, are dropped;
- a business (layer) left with no visible table is dropped.
The validator says "Table x is not allowed." for a hidden table and a missing one alike, so its
errors can't be used to find out which tables exist.

Limit: free text is shown as written. A description (hand-written, or drafted by onboarding) that
mentions a hidden table ("Points to customers(customer_id)") still mentions it: no rewriting of
prose is reliable. What is guaranteed is the structure above, and the validator.
"""

from collections.abc import Collection, Iterable, Sequence

from querymind.agent.schemas import FewShotExample
from querymind.db.schema import SchemaInfo
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import tables_read
from querymind.semantic.catalog import Catalog
from querymind.semantic.models import Metric, SemanticLayer


class NoPermittedTablesError(Exception):
    """The role may use no table of this database: there is nothing it could ask. Safe to show."""


def visible_schema(schema: SchemaInfo, allowed: Collection[str]) -> SchemaInfo:
    """The allowed tables only, without foreign keys that point at a hidden table."""
    return SchemaInfo(
        tables=tuple(
            table.model_copy(
                update={
                    "foreign_keys": tuple(
                        fk for fk in table.foreign_keys if fk.ref_table in allowed
                    )
                }
            )
            for table in schema.tables
            if table.name in allowed
        )
    )


def visible_layers(
    layers: Iterable[SemanticLayer], allowed: Collection[str]
) -> list[SemanticLayer]:
    """Each layer cut down to the allowed tables; a layer with none of them left is dropped."""
    visible = []
    for layer in layers:
        cut = visible_layer(layer, allowed)
        if cut.table_scope:
            visible.append(cut)
    return visible


def visible_layer(layer: SemanticLayer, allowed: Collection[str]) -> SemanticLayer:
    """A layer's descriptions, terms and joins on allowed tables only."""
    return layer.model_copy(
        update={
            "scope": tuple(t for t in layer.scope if t in allowed),
            "tables": {name: info for name, info in layer.tables.items() if name in allowed},
            "metrics": {
                name: metric
                for name, metric in layer.metrics.items()
                if metric_is_visible(metric, allowed)
            },
            "joins": tuple(j for j in layer.joins if all(t in allowed for t in j.tables)),
        }
    )


def metric_is_visible(metric: Metric, allowed: Collection[str]) -> bool:
    """A business term is shown only if every table its SQL reads is allowed ("key account" is on
    customers but sums order_details: hidden order_details hides it)."""
    return metric.table in allowed and _reads_only(metric.query, allowed)


def visible_examples(
    examples: Sequence[FewShotExample], allowed: Collection[str]
) -> list[FewShotExample]:
    """The examples whose SQL reads allowed tables only."""
    return [example for example in examples if _reads_only(example.sql, allowed)]


def _reads_only(sql: str, allowed: Collection[str]) -> bool:
    """True if every table `sql` reads is allowed. Quiet: a hidden table is expected here, not
    an attack, so nothing is logged (the explorer asks on every request)."""
    try:
        return tables_read(sql) <= set(allowed)
    except UnsafeSqlError:
        return False


def visible_catalog(catalog: Catalog, allowed: Collection[str]) -> Catalog:
    """The explorer's table list: allowed tables, without foreign keys to hidden ones."""
    return Catalog(
        tables=[
            table.model_copy(
                update={
                    "foreign_keys": [fk for fk in table.foreign_keys if fk.ref_table in allowed]
                }
            )
            for table in catalog.tables
            if table.name in allowed
        ]
    )
