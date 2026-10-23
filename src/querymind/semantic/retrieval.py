"""Schema retrieval (3.3): send the LLM only the tables a question needs, not all 25.

    retriever = SchemaRetriever(catalog=catalog, embeddings=embeddings, connect=connect_readonly)
    selection = retriever.select("Which music customers live in Brazil?", history=[])
    selection.schema_text      # Table customer ... (+ its business definitions)
    selection.info.tables      # ("customer", ...)

How a question becomes a table list:
  1. Search (RETRIEVAL_MODE, 3.4): rank the tables by
     - `vector`: embedding similarity (meaning: "clients" finds customers);
     - `keyword`: Postgres full-text search (exact words: "category" finds categories);
     - `hybrid` (default): both lists fused with Reciprocal Rank Fusion, see `fuse`;
     - `hybrid_rerank`: hybrid, then one LLM call reorders the top 10 (an experiment arm).
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

It never leaves the LLM without a schema. If the embedding call fails, keyword search carries
on alone (`info.reason` says so); if the search itself fails or finds nothing, the full schema
is sent instead (`info.mode == "fallback"`). Evals count both, so neither can pass for a
retrieval success. A failed rerank keeps the fused order.
"""

import logging
from collections import deque
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from typing import Literal

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from querymind.agent.prompts.rerank import SYSTEM_PROMPT as RERANK_SYSTEM
from querymind.agent.prompts.rerank import build_user_prompt as rerank_prompt
from querymind.agent.schemas import Turn
from querymind.config import Settings
from querymind.db.schema import SchemaInfo, estimate_tokens
from querymind.llm.base import LLMProvider
from querymind.llm.embeddings import EmbeddingProvider
from querymind.llm.errors import LLMError, LLMProviderError
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.index import INDEX_TABLE, IndexHit, keyword_search, search
from querymind.semantic.models import Metric, SemanticLayer
from querymind.semantic.render import schema_prompt_text

logger = logging.getLogger(__name__)

MAX_BRIDGE = 2  # at most this many tables between two hits (customers → orders → order_details)
RRF_K = 60  # the usual Reciprocal Rank Fusion constant: dampens the gap between rank 1 and 2
SEARCH_DEPTH = 15  # documents each search returns (a table + its metrics can take several)
RERANK_CANDIDATES = 10  # tables the reranker may reorder
RERANK_TEXT_CHARS = 400  # of each candidate's document, so the rerank call stays ~1.5k tokens


class RerankResult(BaseModel):
    """The reranker's answer: candidate names, most needed first."""

    tables: list[str] = Field(
        description="The candidate table names, the ones needed to answer the question first"
    )


def fuse(rankings: Sequence[Sequence[str]], *, k: int = RRF_K) -> list[str]:
    """Reciprocal Rank Fusion: score = sum of 1 / (k + rank) over the lists an item is in.

    Ranks, not scores, are combined: a cosine similarity and a ts_rank can't be compared, but
    "2nd in one list, 1st in the other" can. Being in both lists beats being first in one.
    Ties go to the better single rank, then the name, so the order is the same every run.
    """
    scores: dict[str, float] = {}
    best: dict[str, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1 / (k + rank)
            best[item] = min(best.get(item, rank), rank)
    return sorted(scores, key=lambda item: (-scores[item], best[item], item))


class SchemaSelection(BaseModel):
    """What the LLM was shown for one question, and why. Saved in the agent state and audit."""

    model_config = ConfigDict(frozen=True)

    mode: Literal["full", "retrieved", "fallback"]
    tables: tuple[str, ...]  # in the prompt; every table for "full" and "fallback"
    hits: tuple[str, ...] = ()  # found by the search, best first
    added: tuple[str, ...] = ()  # brought in by a follow-up or a join path
    search: str = ""  # the search that ran: vector, keyword, hybrid, hybrid_rerank (3.4)
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
    """Picks the tables for each question (search + carry-over + metric needs + bridges)."""

    def __init__(
        self,
        *,
        catalog: SchemaCatalog,
        embeddings: EmbeddingProvider,
        connect: Callable[[Settings], psycopg.Connection],
        settings: Settings,
        index_table: str = INDEX_TABLE,
        llm: LLMProvider | None = None,
        documents: Mapping[str, str] | None = None,
    ) -> None:
        """`llm` and `documents` (table → its document text) are for `hybrid_rerank` only.

        Raises:
            ValueError: RETRIEVAL_MODE=hybrid_rerank without an `llm`.
        """
        if settings.retrieval_mode == "hybrid_rerank" and llm is None:
            raise ValueError("RETRIEVAL_MODE=hybrid_rerank needs an LLM for the rerank call")
        self.catalog = catalog
        self._embeddings = embeddings
        self._connect = connect  # read-only: the index is only searched here
        self._settings = settings
        self._index_table = index_table
        self._llm = llm
        self._documents = dict(documents or {})

    def select(self, question: str, *, history: Sequence[Turn] = ()) -> Selection:
        """The schema text for this question. Never raises for an embedding or search failure:
        keyword search carries on alone, or the full schema is sent, and the selection says so."""
        try:
            ranked, used, note = self._rank(question)
        except psycopg.Error as exc:
            logger.warning(
                "schema retrieval failed, sending the full schema", extra={"error": str(exc)}
            )
            return self.catalog.full(mode="fallback", reason=f"{type(exc).__name__}: {exc}")
        if not ranked:  # an empty index, or no word of the question in keyword-only search
            reason = note or "the search found no matching documents"
            return self.catalog.full(mode="fallback", reason=reason)
        hits = ranked[: self._settings.retrieval_top_k]

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
            search=used,
            reason=note,
            schema_tokens=estimate_tokens(schema_text),
        )
        return Selection(
            schema_text=schema_text, repair_schema_text=self.catalog.text(widened), info=info
        )

    def _rank(self, question: str) -> tuple[list[str], str, str]:
        """Every matching table, best first; the search that really ran; and a note if a step
        failed (embeddings → keyword only; rerank → fused order).

        The question is embedded *before* the connection opens: a slow embedding API never
        holds a database connection.
        """
        mode = self._settings.retrieval_mode
        vector: list[float] | None = None
        note = ""
        if mode != "keyword":
            try:
                vector = self._embeddings.embed_query(question)
            except LLMProviderError as exc:
                note = f"embeddings failed, keyword search only: {exc}"
                logger.warning("embedding failed, keyword search only", extra={"error": str(exc)})
        with self._connect(self._settings) as conn:
            by_meaning = (
                self._tables(search(conn, vector, limit=SEARCH_DEPTH, table=self._index_table))
                if vector is not None
                else []
            )
            by_words = (
                self._tables(
                    keyword_search(conn, question, limit=SEARCH_DEPTH, table=self._index_table)
                )
                if mode != "vector" or vector is None
                else []
            )
        if vector is None or mode == "keyword":
            return by_words, "keyword", note
        if mode == "vector":
            return by_meaning, "vector", note
        fused = fuse([by_meaning, by_words])
        if mode == "hybrid_rerank":
            reranked, rerank_note = self._rerank(question, fused)
            return reranked, "hybrid" if rerank_note else "hybrid_rerank", rerank_note
        return fused, "hybrid", note

    def _tables(self, hits: Sequence[IndexHit]) -> list[str]:
        """Distinct tables in hit order (a metric hit is its table), visible tables only."""
        tables: list[str] = []
        for hit in hits:
            if hit.table in self.catalog.schema.table_names and hit.table not in tables:
                tables.append(hit.table)
        return tables

    def _rerank(self, question: str, ranked: list[str]) -> tuple[list[str], str]:
        """One LLM call reorders the top candidates. It can only reorder: names it invents are
        dropped, candidates it leaves out keep their fused order after the ones it ranked."""
        assert self._llm is not None  # checked in __init__
        candidates = ranked[:RERANK_CANDIDATES]
        described = [
            (t, self._documents.get(t, f"Table {t}")[:RERANK_TEXT_CHARS]) for t in candidates
        ]
        try:
            result = self._llm.generate_structured(
                rerank_prompt(question=question, candidates=described),
                RerankResult,
                system=RERANK_SYSTEM,
            )
        except LLMError as exc:
            logger.warning("rerank failed, keeping the fused order", extra={"error": str(exc)})
            return ranked, f"rerank failed, fused order kept: {exc}"
        order = [t for t in dict.fromkeys(result.tables) if t in candidates]
        rest = [t for t in candidates if t not in order]
        return order + rest + ranked[RERANK_CANDIDATES:], ""


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
