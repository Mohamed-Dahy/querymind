"""Follow-up → standalone question, for schema retrieval (3.6).

    search_question = rewrite_question(llm, question="Who looks after them?", history=turns)

Only called when there is history (and QUERY_REWRITING is on): a first question is already
standalone, so it costs nothing. The rewrite is used to *search*, next to the question as typed
(fused by rank); the SQL prompt keeps the original question and the history, as in 2.6. The
tables of the previous answer are carried over either way (semantic/retrieval.py), so a failed
rewrite still leaves a working follow-up.
"""

import logging
from collections.abc import Sequence

from querymind.agent.prompts.rewrite import SYSTEM_PROMPT, build_user_prompt
from querymind.agent.schemas import StandaloneQuestion, Turn
from querymind.llm import LLMProvider

logger = logging.getLogger(__name__)


def rewrite_question(llm: LLMProvider, *, question: str, history: Sequence[Turn]) -> str:
    """The question rewritten to stand on its own; the question itself when there's no history
    (no LLM call) or the LLM returns nothing.

    Raises:
        LLMProviderError / LLMOutputError: from the LLM interface, passed through unchanged.
    """
    if not history:
        return question
    result = llm.generate_structured(
        build_user_prompt(question=question, history=history),
        StandaloneQuestion,
        system=SYSTEM_PROMPT,
    )
    rewritten = " ".join(result.question.split())
    logger.info("question rewritten", extra={"question": question, "rewritten": rewritten})
    return rewritten or question
