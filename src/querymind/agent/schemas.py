"""Pydantic models for what the LLM must return. The provider validates its answer against these.

The `Field(description=...)` texts are sent to the LLM as part of the JSON schema, so they
work like extra instructions for each field.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from querymind.db.executor import QueryResult

MAX_OPTIONS = 4

ConfidenceLevel = Literal["high", "medium", "low"]


class Clarification(BaseModel):
    """A question back to the user, when theirs has several reasonable meanings (2.4)."""

    model_config = ConfigDict(str_strip_whitespace=True, frozen=True)

    question: str = Field(description="A short question asking which meaning the user wants")
    options: list[str] = Field(
        description="2 to 4 short phrases, one per meaning, each finishing the user's question, "
        "e.g. 'by total revenue', 'by number of orders'"
    )

    @field_validator("options")
    @classmethod
    def _clean_options(cls, options: list[str]) -> list[str]:
        """Drop blanks and repeats, keep at most 4: the UI shows one button per option."""
        cleaned = list(dict.fromkeys(o.strip() for o in options if o.strip()))
        return cleaned[:MAX_OPTIONS]


class SqlDraft(BaseModel):
    """The LLM's reply to a question: a SQL *draft*, or a clarifying question, or a refusal.

    A draft is not trusted until the validator checks it. The decision fields come first, so
    the LLM decides whether to ask or refuse before it writes any SQL.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    refusal: str | None = Field(
        default=None,
        description="Only if the user asks to change data or settings (insert, update, delete, "
        "create, drop...): one polite sentence saying you can only read data. Otherwise null",
    )
    clarification: Clarification | None = Field(
        default=None,
        description="Only if the question has several reasonable meanings that give clearly "
        "different answers and the schema doesn't settle it. Otherwise null",
    )
    sql: str = Field(
        description="One PostgreSQL SELECT statement that answers the question. Empty if "
        "refusal or clarification is set"
    )
    explanation: str = Field(
        description="One or two plain sentences, for a business user, on what the query does"
    )
    # Reported by the LLM, so only informative: the validator (1.3) finds the real tables itself.
    tables_used: list[str] = Field(description="Names of the tables the query reads")
    # One signal of the confidence score (2.5): the LLM's own view, filled in the same call.
    self_assessment: ConfidenceLevel | None = Field(
        default=None,
        description="How sure you are that the query answers the question as the user meant "
        "it: high, medium (some guessing), or low (a lot of guessing). Null if no SQL",
    )

    @model_validator(mode="after")
    def _has_an_answer(self) -> Self:
        """No SQL is fine only when the LLM asks or refuses instead (else: LLMOutputError)."""
        if not self.sql and self.refusal is None and self.clarification is None:
            raise ValueError("sql is empty, and there is no clarification or refusal")
        return self

    @property
    def wants_sql(self) -> bool:
        """False when the LLM refuses or asks back (refusal wins, then clarification)."""
        return self.refusal is None and self.clarification is None


class AnswerSummary(BaseModel):
    """The plain-language answer, and whether it came from the LLM or the safe fallback."""

    model_config = ConfigDict(frozen=True)

    text: str
    # False = the LLM's answer was replaced (it used numbers not in the result, or the LLM
    # failed), and `text` is a templated sentence built from the data.
    from_llm: bool


class SanityWarning(BaseModel):
    """A sign that a result may be wrong although the query ran (agent/sanity.py)."""

    model_config = ConfigDict(frozen=True)

    code: Literal["empty", "duplicate_rows", "negative_total", "absurd_magnitude"]
    message: str  # for people: shown under the answer
    detail: str  # for the LLM: what to check when it repairs the query


class SqlAttempt(BaseModel):
    """One SQL try that failed: shown to the LLM so it can fix it, and kept for the audit."""

    model_config = ConfigDict(frozen=True)

    sql: str  # the validated SQL if it passed the validator, else the LLM's draft
    # "suspicious" = it ran, but the result looked wrong (an empty result, duplicate rows).
    error_kind: Literal["unsafe", "db_error", "timeout", "suspicious"]
    message: str  # for people: what the API would show
    detail: str  # for the LLM: the validator's reason, or Postgres's own short message


class Confidence(BaseModel):
    """How sure QueryMind is of an answer, and why (scored in agent/confidence.py)."""

    model_config = ConfigDict(frozen=True)

    level: ConfidenceLevel
    reasons: tuple[str, ...]  # one plain sentence per signal that lowered it; empty when high
    points: int


class AskResult(BaseModel):
    """Everything one question produced: what ran, what came back, and the answer."""

    model_config = ConfigDict(frozen=True)

    question: str
    sql: str  # the validated SQL that actually ran (with its LIMIT), not the LLM's draft
    explanation: str
    result: QueryResult
    answer: AnswerSummary
    # The tries that failed or looked wrong before this answer (empty = right first time).
    failed_attempts: tuple[SqlAttempt, ...] = ()
    sql_attempts: int = 1  # SQL queries tried for this answer, including the one shown
    warnings: tuple[SanityWarning, ...] = ()  # signs the result may still be wrong
    confidence: Confidence


class ClarificationRequest(BaseModel):
    """The agent's reply when the question is ambiguous: no SQL ran, it asks back (2.4)."""

    model_config = ConfigDict(frozen=True)

    question: str
    clarification: Clarification


class Refusal(BaseModel):
    """The agent's reply to a request it won't do, e.g. changing data: no SQL ran (2.4)."""

    model_config = ConfigDict(frozen=True)

    question: str
    message: str


# What Pipeline.ask can reply: an answer, a question back, or a polite no.
type AgentReply = AskResult | ClarificationRequest | Refusal


TurnKind = Literal["answer", "clarification", "refusal", "failed"]


class Turn(BaseModel):
    """One earlier question of a conversation, as the LLM sees it in the next prompts (2.6).

    Only the question and what QueryMind did with it: the SQL and the shape of its result,
    never the rows (§8.5). That's enough to change the previous query ("now only for 1997").
    """

    model_config = ConfigDict(frozen=True)

    question: str
    kind: TurnKind
    sql: str | None = None  # the validated SQL whose result was answered
    columns: list[str] = []
    row_count: int | None = None
    reply: str | None = None  # the question asked back (+ options), or the refusal


class FewShotExample(BaseModel):
    """A verified question → SQL pair, shown to the LLM as a pattern (3.5).

    Lives in semantic_layer/<db>.examples.yaml; retrieval picks the ones closest to a question.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    question: str = Field(min_length=1)
    sql: str = Field(min_length=1)
