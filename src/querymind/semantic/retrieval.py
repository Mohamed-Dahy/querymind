"""Schema retrieval (3.3): send the LLM only the tables a question needs, not all 25.

    retriever = SchemaRetriever(catalog=catalog, embeddings=embeddings, connect=connect_readonly)
    selection = retriever.select("Which music customers live in Brazil?", history=[])
    selection.schema_text      # Table customer ... (+ its business definitions)
    selection.info.tables      # ("customer", ...)

How a question becomes a table list:
  1. Search: embed the question, find the closest documents in the index (semantic/index.py).
     A metric document counts as its table. Keep the first `top_k` distinct tables.
  2. Carry over (follow-ups, 2.6): "now only for 1997" names no table, so the tables of the
     previous answer's SQL are added.
  3. Metric needs: a business term shown with a table brings the tables its SQL reads
     ("key account" is on customers, but sums order_details).
  4. Bridge: two chosen tables with a table between them in the join graph get that table
     too: customers + products → orders, order_details. Otherwise the LLM can't write the join.
  5. Cap at `max_tables`, keeping the order above (best search hits first).

Retrieval decides *relevance*, never *permission*: the validator still allows every table the
role can read. A repair sees a wider text (the chosen tables plus their direct neighbours in the
join graph), so a query that failed because a table was missed has a way back.

It never leaves the LLM without a schema. If the embedding call or the search fails, the full
schema is sent instead, and the selection says why (`info.mode == "fallback"`): evals count
those, so a fallback can't pass for a retrieval success.
"""

import logging
from collections import deque
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict

from querymind.agent.schemas import Turn
from querymind.config import Settings
from querymind.db.schema import SchemaInfo, estimate_tokens
from querymind.llm.embeddings import EmbeddingProvider
from querymind.llm.errors import LLMProviderError
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.index import INDEX_TABLE, search
from querymind.semantic.models import Metric, SemanticLayer
from querymind.semantic.render import schema_prompt_text

logger = logging.getLogger(__name__)

MAX_BRIDGE = 2  # at most this many tables between two hits (customers → orders → order_details)


class SchemaSelection(BaseModel):
    """What the LLM was shown for one question, and why. Saved in the agent state and audit."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["full", "retrieved", "fallback"]
    tables: tuple[str, ...]  # in the prompt; every table for "full" and "fallback"
    hits: tuple[str, ...] = ()  # found by the search, best first
    added: tuple[str, ...] = ()  # brought in by a follow-up or a join path
    reason: str = ""  # why it fell back
    schema_tokens: int  # estimated size of the schema text


class Selection(BaseModel):
    """The texts for one question: for the first SQL draft, and for repairs."""

    model_config = ConfigDict(frozen=True)

    schema_text: str
    repair_schema_text: str
    info: SchemaSelection


class SchemaCatalog:
    """The whole schema and its business definitions, renderable for any subset of tables."""

    def __init__(self, schema: SchemaInfo, layers: Sequence[SemanticLayer]) -> None:
        self.schema = schema
        self.layers = list(layers)
        self.full_text = schema_prompt_text(schema, self.layers)
        # The join graph: one edge per foreign key and per preferred join, both directions.
        self._links: dict[str, set[str]] = {name: set() for name in schema.table_names}
        edges = [(t.name, fk.ref_table) for t in schema.tables for fk in t.foreign_keys]
        edges += [j.tables for layer in self.layers for j in layer.joins]
        for left, right in edges:
            if left in self._links and right in self._links and left != right:
                self._links[left].add(right)
                self._links[right].add(left)
        # The tables each metric's SQL reads, per table it is shown with: "key account" lives on
        # customers but sums order_details, so showing it without order_details would be half.
        self._metric_needs: dict[str, set[str]] = {}
        for layer in self.layers:
            for metric in layer.metrics.values():
                if metric.table in self._links:
                    needs = _metric_tables(metric, schema.table_names)
                    self._metric_needs.setdefault(metric.table, set()).update(needs)

    def text(self, tables: Collection[str]) -> str:
        """Schema + definitions for these tables only, in the schema's own order."""
        return schema_prompt_text(self.schema, self.layers, tables=tables)

    def metric_needs(self, tables: Sequence[str]) -> list[str]:
        """Tables the metrics shown with `tables` read, that aren't in `tables` yet."""
        return sorted({n for t in tables for n in self._metric_needs.get(t, ())} - set(tables))

    def neighbours(self, tables: Iterable[str]) -> set[str]:
        """The tables one join away from any of `tables`."""
        return {n for t in tables for n in self._links.get(t, ())}

    def bridges(self, tables: Sequence[str]) -> list[str]:
        """Tables on the shortest join path between each pair of `tables` (at most MAX_BRIDGE)."""
        found: list[str] = []
        for i, start in enumerate(tables):
            for end in tables[i + 1 :]:
                path = self._shortest_path(start, end)
                for table in path[1:-1] if path and len(path) - 2 <= MAX_BRIDGE else ():
                    if table not in tables and table not in found:
                        found.append(table)
        return found

    def _shortest_path(self, start: str, end: str) -> list[str] | None:
        """Breadth-first search: the first time `end` is reached is by a shortest path."""
        came_from: dict[str, str | None] = {start: None}
        queue = deque([start])
        while queue:
            table = queue.popleft()
            if table == end:
                path = [table]
                while (step := came_from[path[-1]]) is not None:
                    path.append(step)
                return path[::-1]
            for nxt in sorted(self._links.get(table, ())):  # sorted: same answer every run
                if nxt not in came_from:
                    came_from[nxt] = table
                    queue.append(nxt)
        return None

    def full(self, *, mode: Literal["full", "fallback"], reason: str = "") -> Selection:
        """Every table: the small-schema case, and the fallback when retrieval fails."""
        info = SchemaSelection(
            mode=mode,
            tables=tuple(sorted(self.schema.table_names)),
            reason=reason,
            schema_tokens=estimate_tokens(self.full_text),
        )
        return Selection(schema_text=self.full_text, repair_schema_text=self.full_text, info=info)


class SchemaRetriever:
    """Picks the tables for each question (search + carry-over + bridges)."""

    def __init__(
        self,
        *,
        catalog: SchemaCatalog,
        embeddings: EmbeddingProvider,
        connect: Callable[[Settings], psycopg.Connection],
        settings: Settings,
        index_table: str = INDEX_TABLE,
    ) -> None:
        self.catalog = catalog
        self._embeddings = embeddings
        self._connect = connect  # read-only: the index is only searched here
        self._settings = settings
        self._index_table = index_table

    def select(self, question: str, *, history: Sequence[Turn] = ()) -> Selection:
        """The schema text for this question. Never raises for an embedding or search failure:
        it falls back to the full schema and says so."""
        try:
            hits = self._search(question)
        except (LLMProviderError, psycopg.Error) as exc:
            logger.warning(
                "schema retrieval failed, sending the full schema", extra={"error": str(exc)}
            )
            return self.catalog.full(mode="fallback", reason=f"{type(exc).__name__}: {exc}")
        if not hits:  # an empty index: nothing to choose from
            return self.catalog.full(mode="fallback", reason="the index returned no documents")

        carried = [t for t in _tables_of_last_answer(history, self.catalog.schema) if t not in hits]
        chosen = hits + carried
        chosen += self.catalog.metric_needs(chosen)
        chosen += self.catalog.bridges(chosen)
        tables = chosen[: self._settings.retrieval_max_tables]
        widened = set(tables) | self.catalog.neighbours(tables)
        schema_text = self.catalog.text(tables)
        info = SchemaSelection(
            mode="retrieved",
            tables=tuple(tables),
            hits=tuple(hits),
            added=tuple(t for t in tables if t not in hits),
            schema_tokens=estimate_tokens(schema_text),
        )
        return Selection(
            schema_text=schema_text, repair_schema_text=self.catalog.text(widened), info=info
        )

    def _search(self, question: str) -> list[str]:
        """The best `top_k` distinct tables for the question, best first."""
        vector = self._embeddings.embed_query(question)
        top_k = self._settings.retrieval_top_k
        # Several documents can point to one table (the table + its metrics): ask for more.
        with self._connect(self._settings) as conn:
            found = search(conn, vector, limit=top_k * 3, table=self._index_table)
        tables: list[str] = []
        for hit in found:
            if hit.table in self.catalog.schema.table_names and hit.table not in tables:
                tables.append(hit.table)
        return tables[:top_k]


def _metric_tables(metric: Metric, allowed: Collection[str]) -> set[str]:
    """The tables a metric's SQL reads, found by wrapping it in a query (as its tests do)."""
    if metric.kind == "measure":
        query = f"SELECT {metric.sql} AS value FROM {metric.table}"
    else:
        query = f"SELECT count(*) FROM {metric.table} WHERE {metric.sql}"
    try:
        return set(validate_sql(query, allowed_tables=allowed, max_rows=1).tables)
    except UnsafeSqlError:  # can't happen for a checked layer; then the metric's table alone
        return {metric.table}


def _tables_of_last_answer(history: Sequence[Turn], schema: SchemaInfo) -> list[str]:
    """The tables the previous answer's SQL read: a follow-up most likely needs them again.

    Parsed by the validator (it knows which names are CTEs, not tables). That SQL was validated
    when it ran; if the schema changed since, it's simply not carried over.
    """
    for turn in reversed(history):
        if turn.sql:
            try:
                query = validate_sql(turn.sql, allowed_tables=schema.table_names, max_rows=1)
            except UnsafeSqlError:
                return []
            return sorted(query.tables)
    return []
