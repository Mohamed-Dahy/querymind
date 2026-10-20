"""Load a golden question set (YAML) into checked Pydantic objects.

    golden = load_golden_set(GOLDEN_DIR / "northwind.yaml")
    for item in golden.questions:
        item.id, item.question, item.sql, item.ordered

A typo in the YAML (unknown key, wrong tier, duplicate id) fails loudly here, instead of
silently skewing the accuracy numbers later.
"""

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

GOLDEN_DIR = Path(__file__).parent / "golden"

Tier = Literal["easy", "medium", "hard"]
Tag = Literal[
    "filter",
    "aggregation",
    "join",
    "group_by",
    "date_range",
    "top_n",
    "subquery",
    "self_join",
    "window",
    "cte",
    "set_logic",
]


class GoldenSetError(Exception):
    """The golden set file is missing, not valid YAML, or doesn't match the expected shape."""


class GoldenQuestion(BaseModel):
    # extra="forbid": a misspelt key (e.g. "orderd") is an error, not silently ignored.
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    tier: Tier
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
    # True when the row order is part of the answer (rankings, time series).
    ordered: bool = False
    tags: tuple[Tag, ...] = ()
    notes: str = ""


class GoldenSet(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str
    version: int
    questions: tuple[GoldenQuestion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> Self:
        ids = [q.id for q in self.questions]
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate question ids: {', '.join(duplicates)}")
        return self

    def by_tier(self, tier: Tier) -> list[GoldenQuestion]:
        return [q for q in self.questions if q.tier == tier]


def load_golden_set(path: Path) -> GoldenSet:
    """Read and validate a golden set file.

    Raises:
        GoldenSetError: with the file name and what is wrong.
    """
    try:
        # safe_load, never yaml.load: plain YAML can contain tags like !!python/object that
        # build arbitrary Python objects (= run code) when loaded unsafely.
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return GoldenSet.model_validate(raw)
    except OSError as exc:
        raise GoldenSetError(f"{path}: cannot read file ({exc})") from exc
    except yaml.YAMLError as exc:
        raise GoldenSetError(f"{path}: invalid YAML ({exc})") from exc
    except ValidationError as exc:
        raise GoldenSetError(f"{path}: {exc}") from exc
