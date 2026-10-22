"""FastAPI dependencies: how a route gets the objects it needs.

A route asks for `pipeline: AnswersQuestions = Depends(get_pipeline)` instead of building one,
so the app creates the pipeline once at startup and tests can hand in a fake.
"""

from typing import Protocol

from fastapi import Request

from querymind.agent.schemas import AgentReply


class AnswersQuestions(Protocol):
    """Anything with `ask(question, conversation_id=...) -> AgentReply`: the real Pipeline,
    or a test fake."""

    def ask(self, question: str, *, conversation_id: str | None = None) -> AgentReply: ...


def get_pipeline(request: Request) -> AnswersQuestions:
    """The pipeline created at startup (see main.py), stored on the app."""
    pipeline: AnswersQuestions = request.app.state.pipeline
    return pipeline
