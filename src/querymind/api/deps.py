"""FastAPI dependencies: how a route gets the objects it needs.

A route asks for `pipeline: AnswersQuestions = Depends(get_pipeline)` instead of building one,
so the app creates the pipeline once at startup and tests can hand in a fake.
"""

from collections.abc import Generator
from typing import Protocol

from fastapi import Request

from querymind.agent.events import AgentEvent, Finished
from querymind.agent.schemas import AgentReply
from querymind.db.executor import QueryResult
from querymind.store.conversations import ConversationStore


class AnswersQuestions(Protocol):
    """Anything with `ask(question, conversation_id=...) -> AgentReply`: the real Pipeline,
    or a test fake."""

    def ask(self, question: str, *, conversation_id: str | None = None) -> AgentReply: ...


class StreamsAnswers(Protocol):
    """Anything with `stream(question, conversation_id=...)`, reporting as it goes."""

    def stream(
        self, question: str, *, conversation_id: str | None = None
    ) -> Generator[AgentEvent | Finished]: ...


class ManagesConversations(Protocol):
    """What the conversation routes need from the pipeline."""

    def forget(self, conversation_id: str) -> None: ...

    def rerun(
        self, question: str, sql: str, *, conversation_id: str | None = None
    ) -> QueryResult: ...


def get_pipeline(request: Request) -> AnswersQuestions:
    """The pipeline created at startup (see main.py), stored on the app."""
    pipeline: AnswersQuestions = request.app.state.pipeline
    return pipeline


def get_streaming_pipeline(request: Request) -> StreamsAnswers:
    """The same pipeline, for the streaming route."""
    pipeline: StreamsAnswers = request.app.state.pipeline
    return pipeline


def get_conversation_pipeline(request: Request) -> ManagesConversations:
    """The same pipeline, for the conversation routes."""
    pipeline: ManagesConversations = request.app.state.pipeline
    return pipeline


def get_store(request: Request) -> ConversationStore | None:
    """The conversation store created at startup, or None when history is off."""
    store: ConversationStore | None = request.app.state.store
    return store
