"""Do follow-up questions get the tables they need? Retrieval on conversations (3.6).

    uv run python -m evals.followups                           # 10 conversations, 3 arms
    uv run python -m evals.followups --provider groq --report docs/eval-reports/phase3-followups.md

For each conversation in evals/golden/followups.yaml, the earlier turns become the history the
agent would remember (question + SQL + result shape: their SQL is run for real), and the LAST
question is retrieved three ways:

- `typed`: the follow-up searched as typed, no history (what 3.3 did before carry-over);
- `typed+carry`: as typed, plus the previous answer's tables (3.3/3.4);
- `rewritten+carry`: only the rewrite (a standalone question) searched, plus carry-over;
- `both+carry`: the follow-up as typed *and* its rewrite searched, fused by rank (RRF), plus
  carry-over: what the agent does (3.6).

The needed tables come from the last turn's reference SQL. No SQL is generated; the rewrite
is one LLM call per conversation.
"""

import argparse
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field

from evals.golden_set import GOLDEN_DIR, GoldenQuestion
from evals.retrieval import CachedEmbeddings, Retrieved, score
from querymind.agent.prompts.rewrite import SYSTEM_PROMPT as REWRITE_SYSTEM
from querymind.agent.prompts.rewrite import build_user_prompt as build_rewrite_prompt
from querymind.agent.rewriter import rewrite_question
from querymind.agent.schemas import Turn
from querymind.config import ConfigError, Settings, get_settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.executor import execute_query
from querymind.db.schema import SchemaInfo, estimate_tokens, introspect_schema
from querymind.llm import LLMError, LLMProvider, create_embedding_provider, create_llm_provider
from querymind.safety.sql_validator import validate_sql
from querymind.semantic.examples import load_all_examples
from querymind.semantic.loader import SemanticLayerError, load_semantic_layers
from querymind.semantic.retrieval import SchemaCatalog, SchemaRetriever

FOLLOWUPS = GOLDEN_DIR / "followups.yaml"
ARMS = ("typed", "typed+carry", "rewritten+carry", "both+carry")


class FollowupTurn(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)


class Conversation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    turns: tuple[FollowupTurn, ...] = Field(min_length=2)

    @property
    def last(self) -> FollowupTurn:
        return self.turns[-1]


class FollowupSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int
    conversations: tuple[Conversation, ...]


def load_followups(path: Path = FOLLOWUPS) -> FollowupSet:
    return FollowupSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def history_of(
    conversation: Conversation, conn: psycopg.Connection, schema: SchemaInfo, settings: Settings
) -> list[Turn]:
    """The earlier turns as the agent remembers them: question, validated SQL, result shape."""
    turns = []
    for turn in conversation.turns[:-1]:
        query = validate_sql(
            turn.sql, allowed_tables=schema.table_names, max_rows=settings.max_rows
        )
        result = execute_query(conn, query, timeout_seconds=settings.query_timeout_seconds)
        turns.append(
            Turn(
                question=turn.question,
                kind="answer",
                sql=query.sql,
                columns=result.columns,
                row_count=result.row_count,
            )
        )
    return turns


def run(
    conversations: Sequence[Conversation],
    *,
    retriever: SchemaRetriever,
    llm: LLMProvider,
    histories: dict[str, list[Turn]],
    delay_seconds: float = 0.0,
) -> tuple[dict[str, list[Retrieved]], dict[str, str], int]:
    """Every arm for every conversation; also the rewritten questions and their prompt tokens."""
    results: dict[str, list[Retrieved]] = {arm: [] for arm in ARMS}
    rewrites: dict[str, str] = {}
    rewrite_tokens = 0
    schema = retriever.catalog.schema
    for number, conversation in enumerate(conversations):
        question, history = conversation.last.question, histories[conversation.id]
        expected = validate_sql(
            conversation.last.sql, allowed_tables=schema.table_names, max_rows=1
        ).tables
        if number and delay_seconds:
            time.sleep(delay_seconds)
        try:
            rewritten = rewrite_question(llm, question=question, history=history)
        except LLMError as exc:  # the agent searches the question as typed then; so do we
            print(f"  rewrite failed for {conversation.id}: {exc}", file=sys.stderr)
            rewritten = question
        rewrites[conversation.id] = rewritten
        prompt = REWRITE_SYSTEM + build_rewrite_prompt(question=question, history=history)
        rewrite_tokens += estimate_tokens(prompt)  # what the rewrite call really sends
        for arm, search, turns, also in (
            ("typed", question, [], ()),
            ("typed+carry", question, history, ()),
            ("rewritten+carry", rewritten, history, ()),
            ("both+carry", question, history, (rewritten,)),
        ):
            started = time.perf_counter()
            selection = retriever.select(search, history=turns, also=also).info
            seconds = time.perf_counter() - started
            results[arm].append(Retrieved(_as_golden(conversation), expected, selection, seconds))
    return results, rewrites, rewrite_tokens // max(len(conversations), 1)


def _as_golden(conversation: Conversation) -> GoldenQuestion:
    """The last turn as a golden question, so the recall eval's scoring can be reused."""
    last = conversation.last
    return GoldenQuestion(id=conversation.id, tier="medium", question=last.question, sql=last.sql)


def build_report(
    results: dict[str, list[Retrieved]],
    rewrites: dict[str, str],
    *,
    meta: dict[str, str],
    rewrite_tokens: int,
) -> str:
    lines = ["# QueryMind eval: follow-up retrieval (query rewriting)", ""]
    lines += [f"- **{key}:** {value}" for key, value in meta.items()]
    lines += [
        "",
        "## By arm",
        "",
        "Recall = share of the last question's needed tables that reached the prompt; complete =",
        "all of them did. `typed` = the follow-up searched as typed, no history; `+carry` = the",
        "previous answer's tables added; `rewritten` = only its standalone rewrite searched;",
        "`both` = typed and rewritten both searched, fused by rank (what the agent does).",
        "",
        "| Arm | Recall | Complete | Tables | Schema tokens |",
        "|---|---|---|---|---|",
    ]
    for arm, rs in results.items():
        s = score(rs, mode=arm, k=0)
        lines.append(
            f"| {arm} | {s.recall:.1%} | {s.complete}/{s.questions} | {s.tables:.1f} "
            f"| ~{s.tokens:,.0f} |"
        )
    lines += [
        "",
        f"The rewrite costs one LLM call per follow-up (~{rewrite_tokens:,} input tokens).",
        "",
    ]
    lines += [
        "## Per conversation",
        "",
        "| Conversation | Follow-up | Rewritten | Needs "
        "| Missed: typed / +carry / rewritten / both |",
        "|---|---|---|---|---|",
    ]
    for row in zip(*(results[arm] for arm in ARMS), strict=True):
        a = row[0]
        missed = " / ".join(", ".join(r.missed) or "-" for r in row)
        question = a.item.question.replace("|", "\\|")
        lines.append(
            f"| `{a.item.id}` | {question} | {rewrites[a.item.id]} "
            f"| {', '.join(sorted(a.expected))} | {missed} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure follow-up retrieval with/without rewriting."
    )
    parser.add_argument("--provider", choices=["groq", "gemini"], help="the rewriter's LLM")
    parser.add_argument("--delay", type=float, default=2.0, help="seconds between rewrite calls")
    parser.add_argument("--report", type=Path, help="write a markdown report to this file")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="replace")  # type: ignore[union-attr]

    followups = load_followups()
    try:
        settings = get_settings()
        if args.provider:
            settings = settings.model_copy(update={"llm_provider": args.provider})
        embeddings = create_embedding_provider(settings)
        if embeddings is None:
            print("Error: GOOGLE_API_KEY is not set: retrieval needs embeddings.", file=sys.stderr)
            return 1
        llm = create_llm_provider(settings)
        layers = load_semantic_layers(settings.semantic_layer_paths)
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn)
            histories = {
                c.id: history_of(c, conn, schema, settings) for c in followups.conversations
            }
    except (ConfigError, SemanticLayerError, psycopg.Error) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DatabaseUnavailableError as exc:
        print(f"Error: {exc.__cause__}", file=sys.stderr)
        print("Is the database running? docker compose up -d --wait", file=sys.stderr)
        return 1

    catalog = SchemaCatalog(schema, layers, load_all_examples(settings.semantic_layer_paths))
    retriever = SchemaRetriever(
        catalog=catalog,
        embeddings=CachedEmbeddings(embeddings),
        connect=connect_readonly,
        settings=settings,
    )
    model, _ = settings.llm_credentials()
    print(
        f"{len(followups.conversations)} conversations, "
        f"rewriter {settings.llm_provider} / {model}\n"
    )
    results, rewrites, rewrite_tokens = run(
        followups.conversations,
        retriever=retriever,
        llm=llm,
        histories=histories,
        delay_seconds=args.delay,
    )
    for arm, rs in results.items():
        s = score(rs, mode=arm, k=0)
        print(
            f"{arm:16} recall {s.recall:.1%}, complete {s.complete}/{s.questions}, "
            f"{s.tables:.1f} tables"
        )
    print()
    for conversation in followups.conversations:
        print(
            f"  {conversation.id}: {conversation.last.question!r} → {rewrites[conversation.id]!r}"
        )
    if args.report:
        meta = {
            "Date": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            "Conversations": str(len(followups.conversations)),
            "Rewriter": f"{settings.llm_provider} / {model}",
            "Retrieval": f"{settings.retrieval_mode}, top-k {settings.retrieval_top_k}, "
            f"max {settings.retrieval_max_tables} tables",
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            build_report(results, rewrites, meta=meta, rewrite_tokens=rewrite_tokens),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
