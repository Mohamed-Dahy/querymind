"""Request and response bodies of the HTTP API (they also define the OpenAPI docs at /docs)."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from querymind.agent.schemas import AskResult


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(
        min_length=1,
        max_length=1000,
        description="A question about the data, in plain language",
        examples=["Which 5 products are the most expensive?"],
    )


class AskResponse(BaseModel):
    """The answer, plus everything needed to check it: the SQL that ran and its rows."""

    question: str
    answer: str = Field(description="Plain-language answer")
    answer_from_llm: bool = Field(
        description="False if the LLM's answer was replaced by a safe templated one"
    )
    sql: str = Field(description="The validated SQL that actually ran")
    explanation: str = Field(description="What the SQL does, in plain words")
    columns: list[str]
    rows: list[list[JsonValue]]
    row_count: int
    truncated: bool = Field(description="True if more rows existed than the row limit")

    @classmethod
    def from_result(cls, result: AskResult) -> "AskResponse":
        return cls(
            question=result.question,
            answer=result.answer.text,
            answer_from_llm=result.answer.from_llm,
            sql=result.sql,
            explanation=result.explanation,
            columns=result.result.columns,
            rows=result.result.rows,
            row_count=result.result.row_count,
            truncated=result.result.truncated,
        )


class ErrorResponse(BaseModel):
    """Every error has the same shape: a stable code for programs, a message for people."""

    error: str = Field(description="Stable error code, e.g. unsafe_sql", examples=["unsafe_sql"])
    message: str = Field(examples=["Only SELECT queries are allowed."])


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
