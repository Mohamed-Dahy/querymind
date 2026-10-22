"""Request and response bodies of the HTTP API (they also define the OpenAPI docs at /docs)."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from querymind.agent.schemas import AgentReply, AskResult, ClarificationRequest, ConfidenceLevel


class AskRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(
        min_length=1,
        max_length=1000,
        description="A question about the data, in plain language",
        examples=["Which 5 products are the most expensive?"],
    )
    conversation_id: UUID | None = Field(
        default=None,
        description="Continue this conversation (from an earlier reply), so follow-ups like "
        '"now only for 1997" work. Leave it out to start a new one.',
    )


class ResultWarning(BaseModel):
    """A sign that the result may be wrong although the query ran (see agent/sanity.py)."""

    code: str = Field(examples=["duplicate_rows"])
    message: str = Field(
        examples=["Some rows appear more than once, which can mean a join counted things twice."]
    )


class AskResponse(BaseModel):
    """The answer, plus everything needed to check it: the SQL that ran and its rows."""

    kind: Literal["answer"] = "answer"
    question: str
    conversation_id: UUID = Field(description="Send it with the next question to follow up")
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
    warnings: list[ResultWarning] = Field(
        default=[], description="Signs the result may be wrong (empty, duplicated rows...)"
    )
    confidence: ConfidenceLevel = Field(description="How sure QueryMind is: high, medium or low")
    confidence_reasons: list[str] = Field(
        description="Why it isn't high (empty when it is)",
        examples=[["It needed 1 repair to get this result."]],
    )

    @classmethod
    def from_result(cls, result: AskResult, *, conversation_id: UUID) -> "AskResponse":
        return cls(
            question=result.question,
            conversation_id=conversation_id,
            answer=result.answer.text,
            answer_from_llm=result.answer.from_llm,
            sql=result.sql,
            explanation=result.explanation,
            columns=result.result.columns,
            rows=result.result.rows,
            row_count=result.result.row_count,
            truncated=result.result.truncated,
            # Only code + message: a warning's `detail` is a hint written for the LLM.
            warnings=[ResultWarning(code=w.code, message=w.message) for w in result.warnings],
            confidence=result.confidence.level,
            confidence_reasons=list(result.confidence.reasons),
        )


class ClarificationResponse(BaseModel):
    """The question had several reasonable meanings: pick one, then ask again. No SQL ran."""

    kind: Literal["clarification"] = "clarification"
    question: str
    conversation_id: UUID = Field(description="Send it with the next question to follow up")
    clarifying_question: str = Field(examples=['How would you define "best" customers?'])
    options: list[str] = Field(examples=[["by total revenue", "by number of orders"]])

    @classmethod
    def from_request(
        cls, request: ClarificationRequest, *, conversation_id: UUID
    ) -> "ClarificationResponse":
        return cls(
            question=request.question,
            conversation_id=conversation_id,
            clarifying_question=request.clarification.question,
            options=request.clarification.options,
        )


class RefusalResponse(BaseModel):
    """QueryMind won't do this (e.g. change data). No SQL ran."""

    kind: Literal["refusal"] = "refusal"
    question: str
    conversation_id: UUID = Field(description="Send it with the next question to follow up")
    message: str = Field(examples=["I can only read data, not change it."])


# The body of a successful POST /ask: `kind` tells which of the three it is.
AskReply = Annotated[
    AskResponse | ClarificationResponse | RefusalResponse, Field(discriminator="kind")
]


def reply_from(
    reply: AgentReply, *, conversation_id: UUID
) -> AskResponse | ClarificationResponse | RefusalResponse:
    """The agent's reply as the matching API body, with the conversation to continue."""
    if isinstance(reply, AskResult):
        return AskResponse.from_result(reply, conversation_id=conversation_id)
    if isinstance(reply, ClarificationRequest):
        return ClarificationResponse.from_request(reply, conversation_id=conversation_id)
    return RefusalResponse(
        question=reply.question, conversation_id=conversation_id, message=reply.message
    )


class ErrorResponse(BaseModel):
    """Every error has the same shape: a stable code for programs, a message for people."""

    error: str = Field(description="Stable error code, e.g. unsafe_sql", examples=["unsafe_sql"])
    message: str = Field(examples=["Only SELECT queries are allowed."])


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
