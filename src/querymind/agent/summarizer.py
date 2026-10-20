"""Query result → short plain-language answer, checked so it never invents numbers.

    summary = summarize(llm, question="How many customers?", result=result)
    summary.text      # "There are 91 customers."
    summary.from_llm  # True; False if the safe fallback was used

Only the first SUMMARY_ROWS rows go to the LLM (CLAUDE.md §8.5: send no more data than needed).
"""

import json
import logging
import unicodedata

from querymind.agent.grounding import find_ungrounded_numbers
from querymind.agent.prompts.summarize import SYSTEM_PROMPT, build_user_prompt
from querymind.agent.schemas import AnswerSummary
from querymind.db.executor import QueryResult
from querymind.llm import LLMError, LLMProvider

logger = logging.getLogger(__name__)

SUMMARY_ROWS = 20


def summarize(llm: LLMProvider, *, question: str, result: QueryResult) -> AnswerSummary:
    """Ask the LLM to answer `question` from `result`; fall back to a template if it can't.

    Never raises for LLM problems: the data is already there, so a failed summary should not
    fail the whole question. The fallback is logged instead.
    """
    if not result.rows:
        # Nothing to explain, so no LLM call (saves a request and can't hallucinate).
        return AnswerSummary(text="No data matched the question.", from_llm=False)

    shown = result.rows[:SUMMARY_ROWS]
    prompt = build_user_prompt(
        question=question,
        result_json=json.dumps({"columns": result.columns, "rows": shown}, ensure_ascii=False),
        shown=len(shown),
        total=result.row_count,
        truncated=result.truncated,
    )
    try:
        text = clean_text(llm.generate_text(prompt, system=SYSTEM_PROMPT))
    except LLMError as exc:
        logger.warning("summary failed, using fallback", extra={"error": str(exc)})
        return AnswerSummary(text=fallback_answer(result), from_llm=False)

    # The prompt itself says how many rows were shown ("Showing the first 20 of 21 rows"), so
    # the LLM may repeat that number too.
    ungrounded = find_ungrounded_numbers(
        text, result=result, question=question, also_known=[len(shown)]
    )
    if ungrounded:
        logger.warning(
            "summary used numbers not in the result, using fallback",
            extra={"ungrounded": ungrounded, "llm_answer": text},
        )
        return AnswerSummary(text=fallback_answer(result), from_llm=False)
    return AnswerSummary(text=text, from_llm=True)


def clean_text(text: str) -> str:
    """Plain spaces and hyphens instead of the typographic ones LLMs like (e.g. U+202F).

    NFKC turns the special spaces into normal ones and keeps accents (Côte stays Côte). This
    makes the text easier to display, and lets the grounding check read "28,245" reliably.
    """
    text = unicodedata.normalize("NFKC", text)
    return text.replace("\u2010", "-").replace("\u2011", "-").strip()


def fallback_answer(result: QueryResult) -> str:
    """A plain sentence built only from the data, so it can't contain invented numbers."""
    if not result.rows:
        return "No data matched the question."
    if len(result.columns) == 1 and result.row_count == 1:
        return f"{result.columns[0]}: {result.rows[0][0]}"
    rows = f"{result.row_count} rows" if result.row_count != 1 else "1 row"
    if result.truncated:
        return f"The query returned more than {rows}; the first {rows} are in the table."
    return f"The query returned {rows}; see the table for the details."
