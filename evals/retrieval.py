"""Does schema retrieval find the right tables? Table recall on the golden questions (3.3).

    uv run python -m evals.retrieval                       # all suites, top-k 3, 5 and 8
    uv run python -m evals.retrieval --suite chinook --k 5
    uv run python -m evals.retrieval --report docs/eval-reports/phase3-retrieval-recall.md

The right tables come from each question's *reference SQL* (the validator lists the tables it
reads), so nothing is labelled by hand. Recall = the share of those tables in the prompt; a
question is *complete* when all of them are. A miss means the LLM never saw a table it needed.

Only embedding calls (one per question, reused for every k), no LLM, no SQL generation: cheap
enough to rerun after every change to the documents, the index or the selection rules.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from evals.golden_set import GoldenQuestion, load_golden_set
from evals.run import SUITES
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SchemaInfo, introspect_schema
from querymind.llm.embeddings import EmbeddingProvider
from querymind.llm.factory import create_embedding_provider
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.loader import SemanticLayerError, load_semantic_layers
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever, SchemaSelection


class CachedEmbeddings(EmbeddingProvider):
    """Embeds each question once, however many k values are tried."""

    def __init__(self, inner: EmbeddingProvider) -> None:
        self.name = inner.name
        self._inner = inner
        self._cache: dict[str, list[float]] = {}

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        if text not in self._cache:
            self._cache[text] = self._inner.embed_query(text)
        return self._cache[text]


@dataclass(frozen=True)
class Retrieved:
    """One question at one k: the tables it needed, and what retrieval chose."""

    item: GoldenQuestion
    expected: frozenset[str]
    selection: SchemaSelection

    @property
    def missed(self) -> list[str]:
        return sorted(self.expected - set(self.selection.tables))

    @property
    def recall(self) -> float:
        return 1 - len(self.missed) / len(self.expected) if self.expected else 1.0

    @property
    def search_recall(self) -> float:
        """Recall of the search hits alone, before carry-over and join bridges."""
        found = self.expected & set(self.selection.hits)
        return len(found) / len(self.expected) if self.expected else 1.0


def expected_tables(item: GoldenQuestion, schema: SchemaInfo) -> frozenset[str]:
    """The tables the reference SQL reads (CTE names excluded, as the validator does)."""
    return validate_sql(item.sql, allowed_tables=schema.table_names, max_rows=1).tables


@dataclass(frozen=True)
class KScore:
    k: int
    questions: int
    recall: float  # mean recall
    search_recall: float  # the same, search hits only
    complete: int  # questions with every needed table
    tables: float  # mean tables in the prompt
    tokens: float  # mean schema tokens
    fallbacks: int  # questions that got the full schema because retrieval failed


def score(results: Sequence[Retrieved], k: int) -> KScore:
    n = len(results)
    return KScore(
        k=k,
        questions=n,
        recall=sum(r.recall for r in results) / n,
        search_recall=sum(r.search_recall for r in results) / n,
        complete=sum(not r.missed for r in results),
        tables=sum(len(r.selection.tables) for r in results) / n,
        tokens=sum(r.selection.schema_tokens for r in results) / n,
        fallbacks=sum(r.selection.mode == "fallback" for r in results),
    )


def run(
    items: Sequence[GoldenQuestion],
    *,
    catalog: SchemaCatalog,
    retriever_for: dict[int, SchemaRetriever],
) -> dict[int, list[Retrieved]]:
    results: dict[int, list[Retrieved]] = {}
    for k, retriever in retriever_for.items():
        results[k] = [
            Retrieved(
                item, expected_tables(item, catalog.schema), retriever.select(item.question).info
            )
            for item in items
        ]
    return results


def build_report(
    results: dict[int, list[Retrieved]], *, full_tokens: int, meta: dict[str, str], detail_k: int
) -> str:
    lines = ["# QueryMind eval: schema retrieval recall", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += [
        "",
        "## By top-k",
        "",
        f"The full schema is ~{full_tokens:,} tokens. Recall = share of the needed tables that",
        "reached the prompt; *complete* = every needed table did. *Search only* = before",
        "follow-up carry-over and join bridges.",
        "",
        "| top-k | Recall | Search only | Complete | Tables | Schema tokens | Fallbacks |",
        "|---|---|---|---|---|---|---|",
    ]
    for k, rs in results.items():
        s = score(rs, k)
        lines.append(
            f"| {k} | {s.recall:.1%} | {s.search_recall:.1%} | {s.complete}/{s.questions} "
            f"| {s.tables:.1f} | ~{s.tokens:,.0f} ({s.tokens / full_tokens:.0%}) | {s.fallbacks} |"
        )
    misses = [r for r in results[detail_k] if r.missed]
    lines += ["", f"## Misses at top-k {detail_k}", ""]
    if not misses:
        lines.append("None: every question got every table it needs.")
    else:
        lines += ["| Question | Needs | Missed | Search hits |", "|---|---|---|---|"]
        lines += [
            f"| `{r.item.id}` | {', '.join(sorted(r.expected))} | **{', '.join(r.missed)}** "
            f"| {', '.join(r.selection.hits)} |"
            for r in misses
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure schema retrieval's table recall.")
    parser.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    parser.add_argument("--k", type=int, nargs="+", default=[3, 5, 8], help="top-k values to try")
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    suites = list(SUITES) if args.suite == "all" else [args.suite]
    items = [q for suite in suites for q in load_golden_set(SUITES[suite]).questions]
    try:
        settings = get_settings()
        inner = create_embedding_provider(settings)
        if inner is None:
            print("Error: GOOGLE_API_KEY is not set: retrieval needs embeddings.", file=sys.stderr)
            return 1
        layers = load_semantic_layers(settings.semantic_layer_paths)
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn)
    except (ConfigError, SemanticLayerError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    catalog = SchemaCatalog(schema, layers)
    embeddings = CachedEmbeddings(inner)
    retrievers = {
        k: SchemaRetriever(
            catalog=catalog,
            embeddings=embeddings,
            connect=connect_readonly,
            settings=_with_k(settings, k),
        )
        for k in args.k
    }
    print(f"Retrieving tables for {len(items)} questions ({', '.join(suites)}), k = {args.k}\n")
    results = run(items, catalog=catalog, retriever_for=retrievers)
    full_tokens = catalog.full(mode="full").info.schema_tokens
    for k, rs in results.items():
        s = score(rs, k)
        print(
            f"top-{k}: recall {s.recall:.1%} (search only {s.search_recall:.1%}), "
            f"complete {s.complete}/{s.questions}, {s.tables:.1f} tables, "
            f"~{s.tokens:,.0f} tokens vs {full_tokens:,} full, fallbacks {s.fallbacks}"
        )
    detail_k = 5 if 5 in results else args.k[0]
    for r in results[detail_k]:
        if r.missed:
            print(f"  missed at k={detail_k}: {r.item.id}: {', '.join(r.missed)}")
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Suites": f"{', '.join(suites)} ({len(items)} questions)",
            "Embeddings": embeddings.name,
            "Max tables": str(settings.retrieval_max_tables),
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            build_report(results, full_tokens=full_tokens, meta=meta, detail_k=detail_k),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.report}")
    return 0


def _with_k(settings: Settings, k: int) -> Settings:
    return settings.model_copy(update={"retrieval_top_k": k})


if __name__ == "__main__":
    sys.exit(main())
