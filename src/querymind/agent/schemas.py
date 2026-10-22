"""Pydantic models for what the LLM must return. The provider validates its answer against these.

The `Field(description=...)` texts are sent to the LLM as part of the JSON schema, so they
work like extra instructions for each field.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from querymind.db.executor import QueryResult


class SqlDraft(BaseModel):
    """The LLM's SQL for a question. A *draft*: it is not trusted until the validator checks it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    sql: str = Field(
        min_length=1, description="One PostgreSQL SELECT statement that answers the question"
    )
    explanation: str = Field(
        description="One or two plain sentences, for a business user, on what the query does"
    )
    # Reported by the LLM, so only informative: the validator (1.3) finds the real tables itself.
    tables_used: list[str] = Field(description="Names of the tables the query reads")


class AnswerSummary(BaseModel):
    """The plain-language answer, and whether it came from the LLM or the safe fallback."""

    model_config = ConfigDict(frozen=True)

    text: str
    # False = the LLM's answer was replaced (it used numbers not in the result, or the LLM
    # failed), and `text` is a templated sentence built from the data.
    from_llm: bool


class SqlAttempt(BaseModel):
    """One SQL try that failed: shown to the LLM so it can fix it, and kept for the audit."""

    model_config = ConfigDict(frozen=True)

    sql: str  # the validated SQL if it passed the validator, else the LLM's draft
    error_kind: Literal["unsafe", "db_error", "timeout"]
    message: str  # for people: what the API would show
    detail: str  # for the LLM: the validator's reason, or Postgres's own short message


class AskResult(BaseModel):
    """Everything one question produced: what ran, what came back, and the answer."""

    model_config = ConfigDict(frozen=True)

    question: str
    sql: str  # the validated SQL that actually ran (with its LIMIT), not the LLM's draft
    explanation: str
    result: QueryResult
    answer: AnswerSummary
    # The tries that failed before the one that worked (empty = right first time).
    failed_attempts: tuple[SqlAttempt, ...] = ()

    @property
    def sql_attempts(self) -> int:
        """How many SQL queries it took, the successful one included."""
        return len(self.failed_attempts) + 1
