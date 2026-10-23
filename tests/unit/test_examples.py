"""Few-shot examples (3.5): the files load, the prompt block, and the leakage check.

Leakage is the main risk: an example that copies a golden question would make the few-shot
gain fake. So no example may be a near-copy of any golden question (text) or have the same
reference SQL. The "must fail" cases prove the check catches near-copies at all.
"""

import re
from difflib import SequenceMatcher
from pathlib import Path

import pytest
import sqlglot

from evals.golden_set import GOLDEN_DIR, load_golden_set
from querymind.agent.prompts.sql_generation import (
    MAX_EXAMPLE_TOKENS,
    build_user_prompt,
    format_examples,
)
from querymind.agent.prompts.sql_repair import build_repair_prompt
from querymind.agent.schemas import FewShotExample, SqlAttempt
from querymind.db.schema import estimate_tokens
from querymind.semantic.examples import examples_path, load_all_examples, load_examples
from querymind.semantic.loader import SemanticLayerError

LAYERS = Path(__file__).parents[2] / "semantic_layer"
LAYER_PATHS = [LAYERS / "northwind.yaml", LAYERS / "chinook.yaml"]
EXAMPLES = load_all_examples(LAYER_PATHS)
GOLDEN = [
    q
    for name in ("northwind.yaml", "northwind_business.yaml", "chinook.yaml")
    for q in load_golden_set(GOLDEN_DIR / name).questions
]
TOO_SIMILAR = 0.8  # SequenceMatcher ratio of the normalized questions


def words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, words(a), words(b)).ratio()


def normalized_sql(sql: str) -> str:
    return sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres", normalize=True)


# --- The files --------------------------------------------------------------------------------


def test_both_databases_have_examples_with_unique_ids() -> None:
    assert examples_path(LAYERS / "chinook.yaml").name == "chinook.examples.yaml"
    counts = {p.stem: len(load_examples(examples_path(p)).examples) for p in LAYER_PATHS}  # type: ignore[union-attr]
    assert counts == {"northwind": 20, "chinook": 20}
    assert len({e.id for e in EXAMPLES}) == len(EXAMPLES)
    assert not {e.id for e in EXAMPLES} & {q.id for q in GOLDEN}


def test_a_missing_file_means_no_examples(tmp_path: Path) -> None:
    assert load_all_examples([tmp_path / "nothing.yaml"]) == []


def test_a_broken_or_duplicated_file_is_a_clear_error(tmp_path: Path) -> None:
    broken = tmp_path / "a.examples.yaml"
    broken.write_text("database: a\nexamples:\n  - id: x\n    questoin: typo\n", encoding="utf-8")
    with pytest.raises(SemanticLayerError, match="not a valid examples file"):
        load_examples(broken)
    for name in ("b", "c"):
        (tmp_path / f"{name}.examples.yaml").write_text(
            f"database: {name}\nexamples:\n  - {{id: same, question: q, sql: SELECT 1}}\n",
            encoding="utf-8",
        )
    with pytest.raises(SemanticLayerError, match="used twice: same"):
        load_all_examples([tmp_path / "b.yaml", tmp_path / "c.yaml"])


# --- Leakage ---------------------------------------------------------------------------------


def test_no_example_copies_a_golden_question() -> None:
    near = [
        (e.id, q.id, round(similarity(e.question, q.question), 2))
        for e in EXAMPLES
        for q in GOLDEN
        if similarity(e.question, q.question) >= TOO_SIMILAR
    ]
    assert near == []


def test_no_example_has_a_golden_reference_sql() -> None:
    golden_sql = {normalized_sql(q.sql): q.id for q in GOLDEN}
    same = [(e.id, golden_sql[s]) for e in EXAMPLES if (s := normalized_sql(e.sql)) in golden_sql]
    assert same == []


@pytest.mark.parametrize(
    ("copy", "golden"),
    [
        # Same template, one value changed: the classic leak.
        (
            "List the company names of our key accounts in France.",
            "List the company names of our key accounts in Germany.",
        ),
        ("How many orders were placed in 1998?", "How many orders were placed in 1997?"),
        ("how many customers are there??", "How many customers are there?"),
    ],
)
def test_the_check_catches_near_copies(copy: str, golden: str) -> None:
    assert similarity(copy, golden) >= TOO_SIMILAR


def test_the_sql_check_ignores_case_and_spacing() -> None:
    assert normalized_sql("select  COUNT(*) from Customers") == normalized_sql(
        "SELECT count(*) FROM customers"
    )


# --- The prompt block ----------------------------------------------------------------------------

JAZZ = FewShotExample(
    id="jazz",
    question="Who bought Jazz?",
    sql="SELECT DISTINCT c.first_name\nFROM customer AS c",
)


def test_no_examples_means_exactly_the_old_prompts() -> None:
    # The golden, clarify and conversation evals were measured on these prompts.
    plain = build_user_prompt(schema_text="Table t", question="q")
    assert plain == build_user_prompt(schema_text="Table t", question="q", examples=[])
    assert "<example>" not in plain
    attempt = SqlAttempt(sql="SELECT x", error_kind="db_error", message="m", detail="d")
    repair = build_repair_prompt(schema_text="Table t", question="q", failed=[attempt])
    assert repair == build_repair_prompt(
        schema_text="Table t", question="q", failed=[attempt], examples=[]
    )


def test_examples_go_between_the_schema_and_the_question() -> None:
    prompt = build_user_prompt(schema_text="Table customer", question="q", examples=[JAZZ])
    assert prompt.index("Table customer") < prompt.index("<example>") < prompt.index("<question>")
    assert (
        "Question: Who bought Jazz?\nSQL: SELECT DISTINCT c.first_name FROM customer AS c" in prompt
    )
    assert "follow the schema and the question, not the examples" in prompt


def test_the_repair_prompt_keeps_the_examples() -> None:
    attempt = SqlAttempt(sql="SELECT x", error_kind="db_error", message="m", detail="d")
    repair = build_repair_prompt(schema_text="T", question="q", failed=[attempt], examples=[JAZZ])
    assert "<example>" in repair and "Attempt 1:" in repair


def test_the_examples_block_has_a_token_cap() -> None:
    long = [
        FewShotExample(id=f"e{i}", question=f"Question {i}?", sql="SELECT " + "x, " * 300 + "y")
        for i in range(5)
    ]
    text = format_examples(long)
    assert estimate_tokens(text) <= MAX_EXAMPLE_TOKENS
    assert text.count("<example>") == 2  # closest first, as many as fit
    assert format_examples(long, max_tokens=10) == ""  # not even one fits: no block at all
