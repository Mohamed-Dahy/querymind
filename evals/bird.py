"""BIRD Mini-Dev: a public text-to-SQL benchmark, loaded as questions our eval runner can score.

    questions = load_bird_questions()          # all 500, from data/raw/bird/
    subset = load_subset(questions)            # the fixed 100 (evals/golden/bird_subset.yaml)
    item = to_golden(question, evidence=True)  # → a GoldenQuestion for evals/run.py
    bird_settings(settings, question.database) # → Settings for that question's database

    uv run python -m evals.bird --make-subset  # (re)write evals/golden/bird_subset.yaml

What BIRD is: 500 questions written by people about 11 real-world databases (schools, football,
banking, chemistry...). Each question comes with the SQL that answers it ("gold SQL") and often
an "evidence" hint: a piece of domain knowledge the question needs, e.g.
"Eligible free rate = `Free Meal Count (K-12)` / `Enrollment (K-12)`". Papers and leaderboards
report the share of questions answered right, so our number can be compared with theirs.

The questions file is data (CC BY-SA 4.0), downloaded by data/seed_bird.py into data/raw/bird/
and never committed. Only the ids of our fixed 100-question subset are committed.
"""

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

import sqlglot
import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlglot import exp
from sqlglot.errors import SqlglotError

from evals.golden_set import GOLDEN_DIR, GoldenQuestion, Tier
from querymind.config import Settings

BIRD_DIR = Path(__file__).parents[1] / "data" / "raw" / "bird"
QUESTIONS_FILE = BIRD_DIR / "mini_dev_postgresql.json"
SUBSET_FILE = GOLDEN_DIR / "bird_subset.yaml"

Difficulty = Literal["simple", "moderate", "challenging"]
DIFFICULTIES: tuple[Difficulty, ...] = ("simple", "moderate", "challenging")
# BIRD's three levels, shown in our reports as the golden sets' tiers.
TIER_OF: dict[Difficulty, Tier] = {"simple": "easy", "moderate": "medium", "challenging": "hard"}
SUBSET_SIZE = 100
SUBSET_SEED = 42
# BIRD's biggest gold answer has ~30,000 rows; our usual cap (1,000) would cut answers off
# before they are compared. In an eval the rows never reach the LLM (no summary), so it's safe.
BIRD_MAX_ROWS = 50_000


class BirdError(Exception):
    """The BIRD questions or subset file is missing or not what we expect."""


class BirdQuestion(BaseModel):
    """One BIRD question, exactly as the benchmark file has it."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    question_id: int
    db_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")  # becomes part of a database name
    question: str = Field(min_length=1)
    evidence: str
    sql: str = Field(alias="SQL", min_length=1)
    difficulty: Difficulty

    @property
    def id(self) -> str:
        """Our id for it, e.g. "bird_1471" (golden-set ids start with a letter)."""
        return f"bird_{self.question_id}"

    @property
    def database(self) -> str:
        """The Postgres database data/seed_bird.py loads it into, e.g. "bird_formula_1"."""
        return database_name(self.db_id)


def database_name(db_id: str) -> str:
    return f"bird_{db_id}"


def load_bird_questions(path: Path = QUESTIONS_FILE) -> list[BirdQuestion]:
    """All questions of the BIRD file (500 for Mini-Dev).

    Raises:
        BirdError: the file is missing (run data/seed_bird.py) or doesn't have BIRD's shape.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        questions = [BirdQuestion.model_validate(item) for item in raw]
    except FileNotFoundError as exc:
        raise BirdError(f"{path} not found: run `uv run python data/seed_bird.py` first") from exc
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise BirdError(f"{path}: not a BIRD questions file ({exc})") from exc
    ids = [q.question_id for q in questions]
    if len(set(ids)) != len(ids):
        raise BirdError(f"{path}: duplicate question ids")
    return questions


def stratified_subset(questions: list[BirdQuestion], *, size: int, seed: int) -> list[BirdQuestion]:
    """A random sample with the same mix of difficulties and databases as the whole set.

    "Stratified" = the questions are split into groups (strata) and each group gets its fair
    share of the sample. A plain random 100 could by chance hold 40 hard questions, or none from
    one database; this way the subset's score is a good estimate of the full 500's score.

    Two levels, so the difficulty mix comes out exact: first each difficulty gets its share of
    `size`, then that share is split over the databases. The same seed gives the same subset.
    """
    if not 0 < size <= len(questions):
        raise ValueError(f"size must be between 1 and {len(questions)}")
    rng = random.Random(seed)
    groups: dict[str, dict[str, list[BirdQuestion]]] = defaultdict(lambda: defaultdict(list))
    for q in sorted(questions, key=lambda q: q.question_id):
        groups[q.difficulty][q.db_id].append(q)

    per_difficulty = _allocate({d: _count(dbs) for d, dbs in groups.items()}, size, rng)
    chosen = []
    for difficulty in sorted(groups):
        databases = groups[difficulty]
        per_database = _allocate(
            {db: len(qs) for db, qs in databases.items()}, per_difficulty[difficulty], rng
        )
        for db in sorted(databases):
            chosen += rng.sample(databases[db], per_database[db])
    return sorted(chosen, key=lambda q: q.question_id)


def _count(databases: dict[str, list[BirdQuestion]]) -> int:
    return sum(len(qs) for qs in databases.values())


def _allocate(sizes: dict[str, int], seats: int, rng: random.Random) -> dict[str, int]:
    """Split `seats` over groups in proportion to their sizes (the largest-remainder rule).

    Each group first gets the whole part of its exact share (e.g. 3 of 3.4); the seats left
    over go to the groups with the biggest leftover fractions, so the total is exactly `seats`.
    Equal fractions are ordered by `rng`, so no group always wins a tie.
    """
    total = sum(sizes.values())
    exact = {key: seats * size / total for key, size in sizes.items()}
    shares = {key: int(share) for key, share in exact.items()}
    tiebreak = {key: rng.random() for key in sorted(sizes)}
    by_remainder = sorted(sizes, key=lambda k: (exact[k] - shares[k], tiebreak[k]), reverse=True)
    for key in by_remainder[: seats - sum(shares.values())]:
        shares[key] += 1
    return shares


class BirdSubset(BaseModel):
    """The committed list of question ids every BIRD run uses (evals/golden/bird_subset.yaml)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str  # which BIRD file the ids refer to
    seed: int
    size: int
    question_ids: tuple[int, ...]


def save_subset(subset: list[BirdQuestion], *, seed: int, path: Path = SUBSET_FILE) -> None:
    mix = Counter(q.difficulty for q in subset)
    header = (
        "# The fixed BIRD Mini-Dev subset every run uses (Phase 4.1), made by\n"
        "#   uv run python -m evals.bird --make-subset\n"
        "# Stratified by difficulty and database, so it mirrors the full 500:\n"
        f"#   {', '.join(f'{d} {mix[d]}' for d in DIFFICULTIES)}.\n"
        "# Only ids: the questions themselves are CC BY-SA data, downloaded by data/seed_bird.py.\n"
    )
    body = BirdSubset(
        source=QUESTIONS_FILE.name,
        seed=seed,
        size=len(subset),
        question_ids=tuple(q.question_id for q in subset),
    ).model_dump()
    body["question_ids"] = list(body["question_ids"])
    path.write_text(header + yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def load_subset(questions: list[BirdQuestion], path: Path = SUBSET_FILE) -> list[BirdQuestion]:
    """The questions whose ids are in the committed subset file, in id order.

    Raises:
        BirdError: the file is invalid, or an id isn't in `questions` (a different BIRD file).
    """
    try:
        subset = BirdSubset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise BirdError(f"{path}: {exc}") from exc
    by_id = {q.question_id: q for q in questions}
    missing = [i for i in subset.question_ids if i not in by_id]
    if missing:
        raise BirdError(f"{path}: ids not in the questions file: {missing[:5]}")
    return [by_id[i] for i in sorted(subset.question_ids)]


def interleave(questions: list[BirdQuestion]) -> list[BirdQuestion]:
    """Round-robin over the databases: the 1st question of each, then the 2nd of each...

    BIRD numbers its questions database by database, so in id order `--limit 20` would only
    reach two or three databases. Interleaved, a quick run touches all eleven.
    """
    ranked: dict[str, int] = defaultdict(int)
    order = []
    for q in sorted(questions, key=lambda q: q.question_id):
        order.append((ranked[q.db_id], q.db_id, q.question_id, q))
        ranked[q.db_id] += 1
    return [q for *_, q in sorted(order, key=lambda entry: entry[:3])]


def question_text(question: BirdQuestion, *, evidence: bool) -> str:
    """What QueryMind is asked. With evidence (BIRD's standard setting), the hint follows the
    question, the way a colleague would add "by the way, eligible rate means X / Y"."""
    if evidence and question.evidence.strip():
        return f"{question.question}\nHint: {question.evidence.strip()}"
    return question.question


def to_golden(question: BirdQuestion, *, evidence: bool = True) -> GoldenQuestion:
    """The same question in the shape evals/run.py scores (id, tier, question, reference SQL)."""
    return GoldenQuestion(
        id=question.id,
        tier=TIER_OF[question.difficulty],
        question=question_text(question, evidence=evidence),
        sql=question.sql,
        ordered=orders_rows(question.sql),
    )


def orders_rows(sql: str) -> bool:
    """True when the gold SQL's outer query has an ORDER BY that shapes the answer.

    Then the row order is part of the answer ("list ... from highest to lowest"). An ORDER BY
    used only to pick the top row (ORDER BY ... LIMIT 1) gives one row, so order can't matter.
    """
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except SqlglotError:
        return False
    if tree.args.get("order") is None:
        return False
    limit = tree.args.get("limit")
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        return limit.expression.name != "1"
    return True


def bird_settings(settings: Settings, database: str) -> Settings:
    """Settings for one BIRD database: its own connection, and BIRD's protocol.

    - The whole schema: BIRD gives a model the whole database (each has at most 13 tables).
    - Nothing of ours in the prompt: no Northwind/Chinook definitions or examples.
    - A row cap big enough for every gold answer (BIRD_MAX_ROWS).
    """
    return settings.model_copy(
        update={
            "postgres_db": database,
            "semantic_layer_paths": [],
            "few_shot_examples": 0,
            "schema_retrieval": "off",
            "max_rows": BIRD_MAX_ROWS,
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="BIRD Mini-Dev helpers.")
    parser.add_argument(
        "--make-subset", action="store_true", help=f"write {SUBSET_FILE.name} (the fixed subset)"
    )
    parser.add_argument("--size", type=int, default=SUBSET_SIZE)
    parser.add_argument("--seed", type=int, default=SUBSET_SEED)
    args = parser.parse_args()
    if not args.make_subset:
        parser.print_help()
        return 0
    try:
        questions = load_bird_questions()
    except BirdError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    subset = stratified_subset(questions, size=args.size, seed=args.seed)
    save_subset(subset, seed=args.seed)
    difficulties = Counter(q.difficulty for q in subset)
    databases = Counter(q.db_id for q in subset)
    print(f"Wrote {len(subset)} question ids to {SUBSET_FILE}")
    print("  " + ", ".join(f"{d}: {difficulties[d]}" for d in DIFFICULTIES))
    print("  " + ", ".join(f"{db}: {n}" for db, n in sorted(databases.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
