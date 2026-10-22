"""Question → SQL draft, using the LLM interface.

    draft = generate_sql(llm, schema_text=describe_schema(schema), question="How many orders?")
    draft.sql  # "SELECT count(*) AS order_count FROM orders"

The result is only a draft: it must pass the SQL validator (Phase 1.3) before it is run.
`repair_sql` asks for a new draft after a failure (Phase 2.2); that draft is validated again.
"""

import logging
from collections.abc import Sequence

from querymind.agent.prompts.sql_generation import SYSTEM_PROMPT, build_user_prompt
from querymind.agent.prompts.sql_repair import build_repair_prompt
from querymind.agent.schemas import SqlAttempt, SqlDraft, Turn
from querymind.llm import LLMProvider

logger = logging.getLogger(__name__)


def generate_sql(
    llm: LLMProvider, *, schema_text: str, question: str, history: Sequence[Turn] = ()
) -> SqlDraft:
    """Ask the LLM for a SQL query that answers `question` on the described schema.

    `history` holds the earlier turns of the conversation, so follow-ups can build on them.

    Raises:
        ValueError: the question is empty.
        LLMProviderError / LLMOutputError: from the LLM interface, passed through unchanged.
    """
    question = question.strip()
    if not question:
        raise ValueError("question is empty")

    draft = llm.generate_structured(
        build_user_prompt(schema_text=schema_text, question=question, history=history),
        SqlDraft,
        system=SYSTEM_PROMPT,
    )
    logger.info(
        "sql generated",
        extra={"provider": llm.name, "sql": draft.sql, "tables_used": draft.tables_used},
    )
    return draft


def repair_sql(
    llm: LLMProvider,
    *,
    schema_text: str,
    question: str,
    failed: Sequence[SqlAttempt],
    history: Sequence[Turn] = (),
) -> SqlDraft:
    """Ask the LLM for a fixed query, showing it every failed attempt and its error.

    Raises:
        ValueError: there is no failed attempt to repair.
        LLMProviderError / LLMOutputError: from the LLM interface, passed through unchanged.
    """
    if not failed:
        raise ValueError("nothing to repair: no failed attempt")
    draft = llm.generate_structured(
        build_repair_prompt(
            schema_text=schema_text, question=question.strip(), failed=failed, history=history
        ),
        SqlDraft,
        system=SYSTEM_PROMPT,
    )
    logger.info(
        "sql repaired",
        extra={"provider": llm.name, "sql": draft.sql, "attempt": len(failed) + 1},
    )
    return draft
