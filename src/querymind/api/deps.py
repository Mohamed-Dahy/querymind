"""FastAPI dependencies: how a route gets the objects it needs.

A route asks for `pipeline: AnswersQuestions = Depends(get_pipeline)` instead of building one,
so the app creates the pipeline once at startup and tests can hand in a fake.
"""

from collections.abc import Generator
from typing import Annotated, Protocol

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from querymind.agent.events import AgentEvent, Finished
from querymind.agent.schemas import AgentReply
from querymind.auth.errors import NotAuthenticatedError
from querymind.auth.tokens import AuthUser, TokenService
from querymind.db.executor import QueryResult
from querymind.store.accounts import AccountStore
from querymind.store.conversations import ConversationStore, Owner
from querymind.store.errors import AccountsUnavailableError


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


# Reads "Authorization: Bearer <token>". auto_error=False: a missing header reaches
# get_current_user, which answers with our own error shape (401 not_authenticated).
_bearer = HTTPBearer(auto_error=False, description="The access token from /auth/login")


def get_tokens(request: Request) -> TokenService | None:
    """The token service created at startup, or None (tests that don't need logins)."""
    tokens: TokenService | None = request.app.state.tokens
    return tokens


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    tokens: Annotated[TokenService | None, Depends(get_tokens)],
) -> AuthUser:
    """Who is asking, from their access token. Every route except the public ones needs it.

    No database read: the token's signature and expiry are enough for its 15 minutes. A disabled
    user stops at the next refresh.

    Raises:
        NotAuthenticatedError: no token, or a bad or expired one (HTTP 401).
    """
    if credentials is None or tokens is None:
        raise NotAuthenticatedError("Please log in.")
    return tokens.read_access(credentials.credentials)


CurrentUser = Annotated[AuthUser, Depends(get_current_user)]


def owner_of(user: AuthUser) -> Owner:
    """What the conversation store scopes by: this user, in their current workspace."""
    return Owner(user_id=user.user_id, workspace_id=user.workspace_id)


def get_accounts(request: Request) -> AccountStore:
    """The account store created at startup.

    Raises:
        AccountsUnavailableError: the app was started without one (HTTP 503).
    """
    accounts: AccountStore | None = request.app.state.accounts
    if accounts is None:
        raise AccountsUnavailableError("Accounts aren't available on this server.")
    return accounts
