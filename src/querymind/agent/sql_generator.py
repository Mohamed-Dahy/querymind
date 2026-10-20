"""Question → SQL draft, using the LLM interface.

    draft = generate_sql(llm, schema_text=describe_schema(schema), question="How many orders?")
    draft.sql  # "SELECT count(*) AS order_count FROM orders"

The result is only a draft: it must pass the SQL validator (Phase 1.3) before it is run.
"""

import logging

from querymind.agent.prompts.sql_generation import SYSTEM_PROMPT, build_user_prompt
from querymind.agent.schemas import SqlDraft
from querymind.llm import LLMProvider

logger = logging.getLogger(__name__)


def generate_sql(llm: LLMProvider, *, schema_text: str, question: str) -> SqlDraft:
    """Ask the LLM for a SQL query that answers `question` on the described schema.

    Raises:
        ValueError: the question is empty.
        LLMProviderError / LLMOutputError: from the LLM interface, passed through unchanged.
    """
    question = question.strip()
    if not question:
        raise ValueError("question is empty")

    draft = llm.generate_structured(
        build_user_prompt(schema_text=schema_text, question=question),
        SqlDraft,
        system=SYSTEM_PROMPT,
    )
    logger.info(
        "sql generated",
        extra={"provider": llm.name, "sql": draft.sql, "tables_used": draft.tables_used},
    )
    return draft
