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
from querymind.api.ratelimit import RateLimits
from querymind.auth.errors import ForbiddenError, NotAuthenticatedError
from querymind.auth.tokens import AuthUser, TokenService
from querymind.db.executor import QueryResult
from querymind.llm import LLMProvider
from querymind.llm.workspace import WorkspaceLLMs
from querymind.store.accounts import AccountStore
from querymind.store.audit import AuditStore
from querymind.store.conversations import ConversationStore, Owner
from querymind.store.errors import AccountsUnavailableError, StoreUnavailableError
from querymind.store.llm_keys import LLMKeyStore


class AnswersQuestions(Protocol):
    """Anything with `ask(question, conversation_id=..., llm=...) -> AgentReply`: the real
    Pipeline, or a test fake. `llm`: the workspace's own provider (5.8), None = the pipeline's."""

    def ask(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
    ) -> AgentReply: ...


class StreamsAnswers(Protocol):
    """Anything with `stream(question, conversation_id=..., llm=...)`, reporting as it goes."""

    def stream(
        self, question: str, *, conversation_id: str | None = None, llm: LLMProvider | None = None
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


def require_admin(user: CurrentUser) -> AuthUser:
    """The current user, if they are an admin of their workspace (the role in their token).

    Raises:
        ForbiddenError: another role (HTTP 403).
    """
    if user.role != "admin":
        raise ForbiddenError("Only a workspace admin can see this.")
    return user


AdminUser = Annotated[AuthUser, Depends(require_admin)]


def get_audit(request: Request) -> AuditStore | None:
    """The audit store created at startup, or None (tests that don't look at it)."""
    audit: AuditStore | None = request.app.state.audit
    return audit


def get_limits(request: Request) -> RateLimits | None:
    """The rate limits created at startup, or None (tests that don't test them: no limits)."""
    limits: RateLimits | None = request.app.state.limits
    return limits


def client_ip(request: Request) -> str:
    """The caller's address, for per-IP limits. The socket's peer: in dev that's the Vite proxy
    (127.0.0.1) for everyone. Behind a load balancer (Phase 6) this must read X-Forwarded-For, but
    only as set by our own proxy: a client can write any value there."""
    return request.client.host if request.client else "unknown"


def limit_questions(
    user: CurrentUser, limits: Annotated[RateLimits | None, Depends(get_limits)]
) -> None:
    """Room for one more question (5.6). After the login check: a 401 comes before a 429.

    Raises:
        RateLimitedError: over a limit (HTTP 429 with Retry-After).
    """
    if limits is not None:
        limits.check_question(user.user_id)


def limit_key_checks(
    user: CurrentUser, limits: Annotated[RateLimits | None, Depends(get_limits)]
) -> None:
    """Room for one more LLM key check (5.8): each one is a call with the key being saved."""
    if limits is not None:
        limits.check_key_check(user.user_id)


def limit_connection_checks(
    user: CurrentUser, limits: Annotated[RateLimits | None, Depends(get_limits)]
) -> None:
    """Room for one more database check (5.9): each one connects out to a typed address."""
    if limits is not None:
        limits.check_connection_check(user.user_id)


def get_llm_keys(request: Request) -> LLMKeyStore:
    """The workspaces' own LLM keys (5.8), created at startup.

    Raises:
        StoreUnavailableError: the app was started without them (HTTP 503).
    """
    keys: LLMKeyStore | None = request.app.state.llm_keys
    if keys is None:
        raise StoreUnavailableError("Saved keys aren't available on this server.")
    return keys


def get_workspace_llms(request: Request) -> WorkspaceLLMs | None:
    """What builds each workspace's own provider, or None (tests: every question on the
    pipeline's own LLM)."""
    llms: WorkspaceLLMs | None = request.app.state.workspace_llms
    return llms


def limit_reruns(
    user: CurrentUser, limits: Annotated[RateLimits | None, Depends(get_limits)]
) -> None:
    """Room for one more re-run (5.6)."""
    if limits is not None:
        limits.check_rerun(user.user_id)
