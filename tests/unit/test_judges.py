"""The DeepEval and RAGAS adapters with the fake LLM: the real metrics run, our fake answers.

Needs the `eval` group (`uv sync --group eval`); skipped where it isn't installed (CI).
"""

import pytest
from pydantic import BaseModel

pytest.importorskip("deepeval")
pytest.importorskip("ragas")

from deepeval.metrics.faithfulness.schema import (
    Claims,
    FaithfulnessScoreReason,
    FaithfulnessVerdict,
    Truths,
    Verdicts,
)
from ragas.metrics.collections.context_precision.util import (
    ContextPrecisionOutput,
)

from evals.judges import context_precision, id_scores, judge_answer
from evals.retrieval import Retrieved
from querymind.llm import LLMProviderError
from querymind.llm.fake import FakeLLMProvider
from querymind.semantic.retrieval import SchemaSelection
from tests.unit.test_quality import ITEM

DATA = '{"columns": ["n"], "rows": [[91]]}\nAll 1 rows are shown.'


def faithfulness_steps(*verdicts: str) -> list[BaseModel]:
    """What the judge answers, step by step, when DeepEval measures Faithfulness."""
    return [
        Truths(truths=["There are 91 customers."]),
        Claims(claims=[f"claim {i}" for i in range(len(verdicts))]),
        Verdicts(verdicts=[FaithfulnessVerdict(verdict=v) for v in verdicts]),
        FaithfulnessScoreReason(reason="one claim is not in the data"),
    ]


def test_faithfulness_runs_through_our_llm_interface() -> None:
    fake = FakeLLMProvider(faithfulness_steps("yes", "no"))
    score, passed, reason = judge_answer(
        fake, "fake", "faithfulness", question="How many?", answer="91, up 5%.", context=DATA
    )
    assert (score, passed, reason) == (0.5, True, "one claim is not in the data")
    steps = [call.schema.__name__ if call.schema else "text" for call in fake.calls]
    assert steps == ["Truths", "Claims", "Verdicts", "FaithfulnessScoreReason"]
    assert DATA in fake.calls[0].prompt  # the judge reads the rows the summarizer saw
    assert "91, up 5%." in fake.calls[1].prompt


def test_a_judge_failure_reaches_the_caller() -> None:
    # judge_case turns it into "not scored"; DeepEval must not swallow it as a score.
    fake = FakeLLMProvider([LLMProviderError("429")])
    with pytest.raises(LLMProviderError):
        judge_answer(fake, "fake", "faithfulness", question="q", answer="a", context=DATA)


def test_ragas_context_precision_rewards_needed_tables_ranked_first() -> None:
    def judged(*verdicts: int) -> float:
        fake = FakeLLMProvider([ContextPrecisionOutput(reason="", verdict=v) for v in verdicts])
        return context_precision(
            fake, question="q", reference="SELECT 1", contexts=[f"table {i}" for i in verdicts]
        )

    assert judged(1, 0) == pytest.approx(1.0)
    assert judged(0, 1) == pytest.approx(0.5)


def test_id_recall_equals_our_own_retrieval_recall() -> None:
    selection = SchemaSelection(mode="retrieved", tables=("orders", "employees"), schema_tokens=0)
    retrieved = Retrieved(ITEM, frozenset({"orders", "customers"}), selection, 0.0)
    precision, recall = id_scores(
        retrieved=list(selection.tables), needed=sorted(retrieved.expected)
    )
    assert recall == retrieved.recall == 0.5
    assert precision == 0.5
