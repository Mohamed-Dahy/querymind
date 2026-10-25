"""Our LLM as the judge for DeepEval and RAGAS (4.5): adapters around `LLMProvider`.

    score, passed, reason = judge_answer(llm, "gemini-3.5-flash", "faithfulness",
                                         question=q, answer=a, context=data)
    precision = context_precision(llm, question=q, reference=sql, contexts=[doc1, doc2])
    precision, recall = id_scores(retrieved=["orders"], needed=["orders", "customers"])

Both libraries normally call OpenAI themselves. Each lets you plug in your own model by
subclassing one class; these adapters forward every judge call to `generate_structured`, so
the judge goes through our `llm/` interface like every other LLM call (logging, token
counts, errors as LLMProviderError, the rate limiter of evals/ratelimit.py).

The only module that imports deepeval and ragas: they are in the optional `eval` group
(`uv sync --group eval`), so evals/quality.py imports this module only when it judges. Their
telemetry is switched off before they are imported, and DeepEval is stopped from loading
`.env` into the environment (it does so on import, which would leak it into tests).
"""

import os
import warnings

os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")

# Imported after the settings above: both libraries read them at import time.
from deepeval.metrics import AnswerRelevancyMetric, FaithfulnessMetric, HallucinationMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase
from pydantic import BaseModel
from ragas.dataset_schema import SingleTurnSample
from ragas.llms.base import InstructorBaseRagasLLM
from ragas.metrics.collections import ContextPrecisionWithReference

with warnings.catch_warnings():
    # RAGAS 0.4 marks these "moved to ragas.metrics.collections", but they aren't there yet.
    warnings.simplefilter("ignore", DeprecationWarning)
    from ragas.metrics import IDBasedContextPrecision, IDBasedContextRecall

from querymind.llm import LLMProvider

#: DeepEval's metrics we run, by the name used in reports. All score 0-1, higher is better
#: (Hallucination too, since DeepEval 4: 1 = nothing in the data is contradicted).
METRICS = {
    "answer_relevancy": AnswerRelevancyMetric,
    "faithfulness": FaithfulnessMetric,
    "hallucination": HallucinationMetric,
}
#: Metrics whose judge also writes a reason (one more call each). Only Faithfulness: its reasons
#: explain where it disagrees with grounding.py; the other two save the call (free-tier quota).
WITH_REASON = {"faithfulness"}


# misc: the base is Any when deepeval isn't installed (CI); no-untyped-call: its own hook.
class DeepEvalJudge(DeepEvalBaseLLM):  # type: ignore[misc, no-untyped-call]
    """DeepEval's model interface. Its metrics pass a Pydantic `schema` for every step.

    The `override` ignores: DeepEval declares these as `(*args, **kwargs)`; ours name the
    arguments its metrics actually pass.
    """

    def __init__(self, llm: LLMProvider, model_name: str) -> None:
        self._llm = llm
        self._model_name = model_name
        super().__init__(model_name)  # calls load_model()

    def load_model(self) -> LLMProvider:  # type: ignore[override]
        return self._llm

    def generate(  # type: ignore[override]
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> str | BaseModel:
        if schema is None:
            return self._llm.generate_text(prompt)
        return self._llm.generate_structured(prompt, schema)

    async def a_generate(  # type: ignore[override]
        self, prompt: str, schema: type[BaseModel] | None = None
    ) -> str | BaseModel:
        # Metrics run with async_mode=False, one call at a time; this only satisfies the ABC.
        return self.generate(prompt, schema)

    def get_model_name(self) -> str:
        return self._model_name


class RagasJudge(InstructorBaseRagasLLM):  # type: ignore[misc]
    """RAGAS's model interface for its newer metrics: a prompt in, a `response_model` out."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    def generate[T: BaseModel](self, prompt: str, response_model: type[T]) -> T:
        return self._llm.generate_structured(prompt, response_model)

    async def agenerate[T: BaseModel](self, prompt: str, response_model: type[T]) -> T:
        # `metric.score()` runs this in its own event loop; the call itself is synchronous.
        return self.generate(prompt, response_model)


def judge_answer(
    llm: LLMProvider, model_name: str, metric: str, *, question: str, answer: str, context: str
) -> tuple[float, bool, str]:
    """One DeepEval metric on one answer: (score, passed its threshold, the judge's reason).

    `context` is the data the answer was written from: Faithfulness reads it as the
    "retrieval context", Hallucination as the "context". Raises whatever the judge raised.
    """
    with warnings.catch_warnings():
        # The notice that Hallucination's direction changed in DeepEval 4 (handled above).
        warnings.simplefilter("ignore", DeprecationWarning)
        # async_mode=False: one judge call at a time, so the rate limiter sees each one.
        scorer = METRICS[metric](
            model=DeepEvalJudge(llm, model_name),
            async_mode=False,
            include_reason=metric in WITH_REASON,
        )
    case = LLMTestCase(
        input=question, actual_output=answer, retrieval_context=[context], context=[context]
    )
    scorer.measure(case, _show_indicator=False)  # no progress spinner in our output
    return float(scorer.score), bool(scorer.is_successful()), str(scorer.reason or "")


def context_precision(
    llm: LLMProvider, *, question: str, reference: str, contexts: list[str]
) -> float:
    """RAGAS LLM context precision: was each retrieved context useful for `reference`?

    One judge call per context. Order counts: useful contexts ranked first score higher.
    """
    metric = ContextPrecisionWithReference(llm=RagasJudge(llm))
    result = metric.score(user_input=question, reference=reference, retrieved_contexts=contexts)
    return float(result.value)


def id_scores(*, retrieved: list[str], needed: list[str]) -> tuple[float, float]:
    """RAGAS ID-based context (precision, recall): which retrieved ids are needed, and which
    needed ids were retrieved. Plain set arithmetic, no LLM."""
    sample = SingleTurnSample(retrieved_context_ids=retrieved, reference_context_ids=needed)
    precision = IDBasedContextPrecision().single_turn_score(sample)
    recall = IDBasedContextRecall().single_turn_score(sample)
    return float(precision), float(recall)
