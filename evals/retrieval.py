"""Does schema retrieval find the right tables? Table recall on the golden questions (3.3, 3.4).

    uv run python -m evals.retrieval                     # vector, keyword, hybrid; k 3 5 8
    uv run python -m evals.retrieval --modes hybrid --k 5
    uv run python -m evals.retrieval --modes hybrid hybrid_rerank --k 5 --provider gemini --delay 5
    uv run python -m evals.retrieval --report docs/eval-reports/phase3-retrieval-modes.md

The right tables come from each question's *reference SQL* (the validator lists the tables it
reads), so nothing is labelled by hand. Recall = the share of those tables in the prompt; a
question is *complete* when all of them are. A miss means the LLM never saw a table it needed.
MRR (mean reciprocal rank) = how high the first needed table ranks in the search hits (1 = first).

`vector`, `keyword` and `hybrid` need embedding calls only (one per question, reused for every
mode and k), no LLM: cheap enough to rerun after every change. `hybrid_rerank` adds one LLM
call per question (cached across k), so it's only run when asked for.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from pydantic import BaseModel

from evals.golden_set import GoldenQuestion, load_golden_set
from evals.run import SUITES
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SchemaInfo, estimate_tokens, introspect_schema
from querymind.llm import LLMProvider, create_llm_provider
from querymind.llm.embeddings import EmbeddingProvider
from querymind.llm.factory import create_embedding_provider
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.documents import index_documents
from querymind.semantic.examples import load_all_examples
from querymind.semantic.loader import SemanticLayerError, load_semantic_layers
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever, SchemaSelection

MODES = ("vector", "keyword", "hybrid", "hybrid_rerank")


class CachedEmbeddings(EmbeddingProvider):
    """Embeds each question once, however many modes and k values are tried."""

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


class CountingLLM(LLMProvider):
    """The reranker's LLM, with each distinct prompt asked once and its tokens counted."""

    def __init__(self, inner: LLMProvider, *, delay_seconds: float = 0.0) -> None:
        self.name = inner.name
        self._inner = inner
        self._delay = delay_seconds
        self._cache: dict[str, BaseModel] = {}
        self.calls = 0
        self.prompt_tokens = 0

    def generate_text(self, prompt: str, *, system: str | None = None) -> str:
        raise NotImplementedError("the reranker only asks for structured output")

    def generate_structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        if prompt not in self._cache:
            if self.calls and self._delay:
                time.sleep(self._delay)  # stay under the provider's requests-per-minute limit
            self.calls += 1
            self.prompt_tokens += estimate_tokens(prompt + (system or ""))
            self._cache[prompt] = self._inner.generate_structured(prompt, schema, system=system)
        cached = self._cache[prompt]
        assert isinstance(cached, schema)
        return cached


@dataclass(frozen=True)
class Retrieved:
    """One question in one mode at one k: the tables it needed, and what retrieval chose."""

    item: GoldenQuestion
    expected: frozenset[str]
    selection: SchemaSelection
    seconds: float

    @property
    def missed(self) -> list[str]:
        return sorted(self.expected - set(self.selection.tables))

    @property
    def recall(self) -> float:
        return 1 - len(self.missed) / len(self.expected) if self.expected else 1.0

    @property
    def search_recall(self) -> float:
        """Recall of the search hits alone, before carry-over, metric needs and join bridges."""
        found = self.expected & set(self.selection.hits)
        return len(found) / len(self.expected) if self.expected else 1.0

    @property
    def reciprocal_rank(self) -> float:
        """1 / the rank of the first needed table among the search hits; 0 if none is there."""
        for rank, table in enumerate(self.selection.hits, start=1):
            if table in self.expected:
                return 1 / rank
        return 0.0


def expected_tables(item: GoldenQuestion, schema: SchemaInfo) -> frozenset[str]:
    """The tables the reference SQL reads (CTE names excluded, as the validator does)."""
    return validate_sql(item.sql, allowed_tables=schema.table_names, max_rows=1).tables


@dataclass(frozen=True)
class Score:
    mode: str
    k: int
    questions: int
    recall: float  # mean recall
    search_recall: float  # the same, search hits only
    complete: int  # questions with every needed table
    mrr: float  # mean reciprocal rank of the first needed table
    tables: float  # mean tables in the prompt
    tokens: float  # mean schema tokens
    fallbacks: int  # questions that got the full schema (or keyword only) because a step failed
    milliseconds: float  # mean time per question (search, + the rerank call)
    examples: float  # mean few-shot examples shown (3.5)


def score(results: Sequence[Retrieved], *, mode: str, k: int) -> Score:
    n = len(results)
    return Score(
        mode=mode,
        k=k,
        questions=n,
        recall=sum(r.recall for r in results) / n,
        search_recall=sum(r.search_recall for r in results) / n,
        complete=sum(not r.missed for r in results),
        mrr=sum(r.reciprocal_rank for r in results) / n,
        tables=sum(len(r.selection.tables) for r in results) / n,
        tokens=sum(r.selection.schema_tokens for r in results) / n,
        fallbacks=sum(r.selection.mode == "fallback" or bool(r.selection.reason) for r in results),
        milliseconds=1000 * sum(r.seconds for r in results) / n,
        examples=sum(len(r.selection.examples) for r in results) / n,
    )


type Results = dict[tuple[str, int], list[Retrieved]]


def run(
    items: Sequence[GoldenQuestion],
    *,
    catalog: SchemaCatalog,
    retrievers: dict[tuple[str, int], SchemaRetriever],
) -> Results:
    results: Results = {}
    for key, retriever in retrievers.items():
        results[key] = []
        for item in items:
            started = time.perf_counter()
            selection = retriever.select(item.question).info
            seconds = time.perf_counter() - started
            expected = expected_tables(item, catalog.schema)
            results[key].append(Retrieved(item, expected, selection, seconds))
    return results


def load_catalog(settings: Settings) -> tuple[SchemaCatalog, dict[str, str]]:
    """What retrieval picks from: the schema catalog, and each table's index document.

    Raises:
        SemanticLayerError: a semantic-layer file is invalid.
        psycopg.Error / DatabaseUnavailableError: the database can't be read.
    """
    layers = load_semantic_layers(settings.semantic_layer_paths)
    with connect_readonly(settings) as conn:
        schema = introspect_schema(conn)
    catalog = SchemaCatalog(schema, layers, load_all_examples(settings.semantic_layer_paths))
    docs = index_documents(schema, layers, settings.semantic_layer_paths)
    return catalog, {d.table: d.text for d in docs if d.kind == "table"}


def build_report(
    results: Results,
    *,
    full_tokens: int,
    meta: dict[str, str],
    detail: tuple[str, int],
    rerank_tokens: float,
) -> str:
    lines = ["# QueryMind eval: schema retrieval", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += [
        "",
        "## By mode and top-k",
        "",
        f"The full schema is ~{full_tokens:,} tokens. Recall = share of the needed tables that",
        "reached the prompt; *complete* = every needed table did; *search only* = before",
        "follow-up carry-over, metric needs and join bridges; MRR = 1 / rank of the first needed",
        "table in the search hits. Time = per question (the first run of a mode includes the",
        "embedding call; the others reuse it).",
        "",
        "| Mode | top-k | Recall | Search only | Complete | MRR | Tables | Schema tokens "
        "| Degraded | Time | Examples |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (mode, k), rs in results.items():
        s = score(rs, mode=mode, k=k)
        lines.append(
            f"| {mode} | {k} | {s.recall:.1%} | {s.search_recall:.1%} "
            f"| {s.complete}/{s.questions} | {s.mrr:.2f} | {s.tables:.1f} "
            f"| ~{s.tokens:,.0f} ({s.tokens / full_tokens:.0%}) | {s.fallbacks} "
            f"| {s.milliseconds:.0f} ms | {s.examples:.1f} |"
        )
    if rerank_tokens:
        lines += ["", f"The rerank call adds ~{rerank_tokens:,.0f} prompt tokens per question."]
    misses = [r for r in results[detail] if r.missed]
    lines += ["", f"## Misses: {detail[0]}, top-k {detail[1]}", ""]
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
    parser.add_argument(
        "--modes", nargs="+", choices=MODES, default=["vector", "keyword", "hybrid"]
    )
    parser.add_argument("--k", type=int, nargs="+", default=[3, 5, 8], help="top-k values to try")
    parser.add_argument("--provider", choices=["groq", "gemini"], help="the reranker's LLM")
    parser.add_argument("--delay", type=float, default=0.0, help="seconds between rerank calls")
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    suites = list(SUITES) if args.suite == "all" else [args.suite]
    items = [q for suite in suites for q in load_golden_set(SUITES[suite]).questions]
    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        inner = create_embedding_provider(settings)
        if inner is None:
            print("Error: GOOGLE_API_KEY is not set: retrieval needs embeddings.", file=sys.stderr)
            return 1
        llm = (
            CountingLLM(create_llm_provider(settings), delay_seconds=args.delay)
            if "hybrid_rerank" in args.modes
            else None
        )
        catalog, documents = load_catalog(settings)
    except (ConfigError, SemanticLayerError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    embeddings = CachedEmbeddings(inner)
    retrievers = {
        (mode, k): SchemaRetriever(
            catalog=catalog,
            embeddings=embeddings,
            connect=connect_readonly,
            settings=settings.model_copy(update={"retrieval_mode": mode, "retrieval_top_k": k}),
            llm=llm,
            documents=documents,
        )
        for mode in args.modes
        for k in args.k
    }
    print(
        f"Retrieving tables for {len(items)} questions ({', '.join(suites)}), "
        f"modes {args.modes}, k = {args.k}\n"
    )
    results = run(items, catalog=catalog, retrievers=retrievers)
    full_tokens = catalog.full(mode="full").info.schema_tokens
    for (mode, k), rs in results.items():
        s = score(rs, mode=mode, k=k)
        print(
            f"{mode:13} top-{k}: recall {s.recall:.1%} (search only {s.search_recall:.1%}), "
            f"complete {s.complete}/{s.questions}, MRR {s.mrr:.2f}, {s.tables:.1f} tables, "
            f"~{s.tokens:,.0f} tokens, degraded {s.fallbacks}, {s.milliseconds:.0f} ms, "
            f"{s.examples:.1f} examples"
        )
    rerank_tokens = llm.prompt_tokens / llm.calls if llm and llm.calls else 0.0
    if llm:
        print(f"\nrerank: {llm.calls} LLM calls, ~{rerank_tokens:,.0f} prompt tokens each")
    detail = next((key for key in [("hybrid", 5), *results] if key in results), next(iter(results)))
    for r in results[detail]:
        if r.missed:
            print(f"  missed ({detail[0]}, k={detail[1]}): {r.item.id}: {', '.join(r.missed)}")
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Suites": f"{', '.join(suites)} ({len(items)} questions)",
            "Embeddings": embeddings.name,
            "Max tables": str(settings.retrieval_max_tables),
        }
        if llm:
            meta["Reranker"] = f"{settings.llm_provider} / {settings.llm_credentials()[0]}"
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            build_report(
                results,
                full_tokens=full_tokens,
                meta=meta,
                detail=detail,
                rerank_tokens=rerank_tokens,
            ),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
