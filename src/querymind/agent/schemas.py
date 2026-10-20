"""Pydantic models for what the LLM must return. The provider validates its answer against these.

The `Field(description=...)` texts are sent to the LLM as part of the JSON schema, so they
work like extra instructions for each field.
"""

from pydantic import BaseModel, ConfigDict, Field


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
