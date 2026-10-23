"""Few-shot examples (3.5): verified question → SQL pairs, one file per semantic layer.

    examples = load_all_examples(settings.semantic_layer_paths)   # northwind + chinook
    examples[0].question, examples[0].sql

semantic_layer/northwind.yaml → semantic_layer/northwind.examples.yaml (a missing file = none).
Schema retrieval embeds each example's *question* into the same index (kind "example") and,
per question, shows the LLM the closest few whose tables are all in its prompt.
"""

from collections.abc import Sequence
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from querymind.agent.schemas import FewShotExample
from querymind.semantic.loader import SemanticLayerError


class ExampleFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database: str
    examples: tuple[FewShotExample, ...] = ()


def examples_path(layer_path: Path) -> Path:
    """semantic_layer/northwind.yaml → semantic_layer/northwind.examples.yaml"""
    return layer_path.with_name(f"{layer_path.stem}.examples.yaml")


def load_examples(path: Path) -> ExampleFile | None:
    """The examples file, or None if there isn't one.

    Raises:
        SemanticLayerError: the file exists but isn't valid YAML or has the wrong shape.
    """
    if not path.exists():
        return None
    try:
        return ExampleFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except (yaml.YAMLError, ValidationError) as exc:
        raise SemanticLayerError(f"{path} is not a valid examples file:\n{exc}") from exc


def load_all_examples(layer_paths: Sequence[Path]) -> list[FewShotExample]:
    """Every layer's examples, in file order.

    Raises:
        SemanticLayerError: a file is broken, or two examples share an id.
    """
    found = [f for f in (load_examples(examples_path(p)) for p in layer_paths) if f is not None]
    examples = [example for file in found for example in file.examples]
    ids = [e.id for e in examples]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SemanticLayerError(f"example ids used twice: {', '.join(duplicates)}")
    return examples
