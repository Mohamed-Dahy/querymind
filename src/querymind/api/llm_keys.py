"""Bring your own LLM key (5.8): which provider, model and key answer a workspace's questions.

    GET    /llm/providers   the fixed list a user can choose from (llm/catalog.py)
    GET    /llm/key         the workspace's choice: provider, model, the key's last 4 characters
    PUT    /llm/key         save or replace it (admins): one test call with the key first
    DELETE /llm/key         back to the demo key (admins)

The key goes in once and never comes out: no reply has a field for it (`LLMKeyInfo`), the error
for a bad key is our own wording (never the provider's reply), and an invalid request body is
answered without its input (main.py's handler). Saving is rate-limited per user: each save is a
real call with the key, and the server must not become a way to try out stolen keys.
"""

import time
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from querymind.api import audit_trail
from querymind.api.deps import (
    AdminUser,
    CurrentUser,
    get_audit,
    get_llm_keys,
    limit_key_checks,
)
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import AuthUser
from querymind.llm.catalog import CATALOG, ProviderId, entry_for
from querymind.llm.factory import create_llm_provider_for
from querymind.llm.key_check import (
    CHECK_MAX_RETRIES,
    CHECK_TIMEOUT_SECONDS,
    KeyCheckError,
    check_key,
)
from querymind.store.audit import Action, AuditEntry, AuditStore
from querymind.store.llm_keys import LLMKeyRecord, LLMKeyStore

router = APIRouter(prefix="/llm", tags=["llm keys"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    503: {"model": ErrorResponse, "description": "Saved keys aren't available"},
}
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
}

# Model ids look like "gpt-4.1-mini", "openai/gpt-oss-120b", "claude-haiku-4-5",
# "meta-llama/Llama-3.3-70B-Instruct-Turbo": letters, digits and . _ : / @ -, nothing else.
MODEL_PATTERN = r"^[A-Za-z0-9._:/@-]{1,200}$"


class ProviderOption(BaseModel):
    """One provider a workspace can bring a key for."""

    id: ProviderId
    label: str
    example_model: str = Field(description="A model it serves, as a hint for the form")
    keys_url: str = Field(description="Where to make a key")


class SaveLLMKeyRequest(BaseModel):
    """What an admin sends to save the workspace's LLM."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderId
    model: str = Field(pattern=MODEL_PATTERN, examples=["openai/gpt-oss-120b"])
    # SecretStr: never printed, logged or put in a reply by mistake. 512: real keys are far
    # shorter; a huge value must not be sent on to the provider.
    api_key: SecretStr = Field(min_length=8, max_length=512)


class LLMKeyInfo(BaseModel):
    """The workspace's own LLM, as the API shows it. There is no field for the key."""

    provider: ProviderId
    provider_label: str
    model: str
    key_hint: str | None = Field(description="The key's last 4 characters, e.g. 'WXYZ'")
    updated_at: datetime

    @classmethod
    def of(cls, record: LLMKeyRecord) -> "LLMKeyInfo":
        return cls(
            provider=record.provider,
            provider_label=entry_for(record.provider).label,
            model=record.model,
            key_hint=record.hint,
            updated_at=record.updated_at,
        )


@router.get("/providers", response_model=list[ProviderOption], responses=_ERRORS)
def list_providers(user: CurrentUser) -> list[ProviderOption]:
    """The providers a workspace can bring its own key for. No free-form address: a fixed list."""
    return [
        ProviderOption(
            id=entry.id,
            label=entry.label,
            example_model=entry.example_model,
            keys_url=entry.keys_url,
        )
        for entry in CATALOG.values()
    ]


@router.get("/key", response_model=LLMKeyInfo | None, responses=_ERRORS)
def get_key(
    user: CurrentUser, keys: Annotated[LLMKeyStore, Depends(get_llm_keys)]
) -> LLMKeyInfo | None:
    """The workspace's own LLM, or `null`: questions then run on the free demo key."""
    record = keys.get(user.workspace_id)
    return LLMKeyInfo.of(record) if record is not None else None


@router.put(
    "/key",
    response_model=LLMKeyInfo,
    responses={
        **_ADMIN_ERRORS,
        422: {"model": ErrorResponse, "description": "The key, the model or the provider failed"},
        429: {"model": ErrorResponse, "description": "Too many keys tried: wait Retry-After"},
    },
    dependencies=[Depends(limit_key_checks)],
)
def save_key(
    request: SaveLLMKeyRequest,
    user: AdminUser,
    keys: Annotated[LLMKeyStore, Depends(get_llm_keys)],
    audit: Annotated[AuditStore | None, Depends(get_audit)],
) -> LLMKeyInfo:
    """Save (or replace) the workspace's provider, model and key, after one test call with it.

    Its questions use it from the next one on. The key is stored encrypted and never shown again:
    only its last 4 characters.
    """
    started = time.perf_counter()
    entry = entry_for(request.provider)
    provider = create_llm_provider_for(
        request.provider,
        request.model,
        request.api_key,
        timeout_seconds=CHECK_TIMEOUT_SECONDS,
        max_retries=CHECK_MAX_RETRIES,
    )
    try:
        check_key(provider, entry)
    except KeyCheckError:
        _write(audit, user, "llm_key_refused", "llm_key_invalid", request, started)
        raise
    record = keys.save(user.workspace_id, request.provider, request.model, request.api_key)
    _write(audit, user, "llm_key_saved", "ok", request, started)
    return LLMKeyInfo.of(record)


@router.delete(
    "/key", status_code=status.HTTP_204_NO_CONTENT, response_class=Response, responses=_ADMIN_ERRORS
)
def delete_key(
    user: AdminUser,
    keys: Annotated[LLMKeyStore, Depends(get_llm_keys)],
    audit: Annotated[AuditStore | None, Depends(get_audit)],
) -> Response:
    """Remove the workspace's own LLM and its key: questions go back to the demo key."""
    started = time.perf_counter()
    if keys.delete(user.workspace_id):
        _write(audit, user, "llm_key_deleted", "ok", None, started)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _write(
    audit: AuditStore | None,
    user: AuthUser,
    action: Action,
    outcome: str,
    request: SaveLLMKeyRequest | None,
    started: float,
) -> None:
    """The key's audit line: who, which provider and model. Never the key."""
    audit_trail.write(
        audit,
        AuditEntry(
            action=action,
            outcome=outcome,
            user_id=user.user_id,
            workspace_id=user.workspace_id,
            email=user.email,
            detail=f"{request.provider} / {request.model}" if request else None,
            duration_ms=audit_trail.elapsed_ms(started),
        ),
    )
