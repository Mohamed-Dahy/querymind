"""Onboarding a saved database and reviewing its data dictionary (5.10).

    POST /connections/{id}/onboard      start a run (admins); also starts by itself on save
    GET  /connections/{id}/onboarding   where it is: queued, reading, drafting, ready, failed
    GET  /connections/{id}/dictionary   the drafted descriptions of its tables and columns
    PUT  /connections/{id}/dictionary   an admin's corrections (and "reviewed" marks)

Drafting uses the workspace's own LLM key (one call per table), so a run needs one. An edit is
refused while a run is going on (its result would overwrite the edit), and only the tables and
columns the dictionary already has can be edited: nothing new can be smuggled into the prompt.
"""

import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from querymind.agent.registry import OwnKeyRequiredError, PipelineRegistry
from querymind.api import audit_trail
from querymind.api.deps import (
    AdminUser,
    CurrentUser,
    get_audit,
    get_connections,
    get_pipelines,
    get_workspace_llms,
    limit_connection_checks,
)
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import AuthUser
from querymind.llm.workspace import WorkspaceLLMs
from querymind.semantic.dictionary import DataDictionary, DictionaryTable
from querymind.semantic.onboarding import Onboarder
from querymind.store.audit import Action, AuditEntry, AuditStore
from querymind.store.connections import ConnectionStore
from querymind.store.errors import (
    DictionaryEditError,
    DictionaryNotFoundError,
    StoreError,
    StoreUnavailableError,
)
from querymind.store.onboarding import OnboardingRecord, OnboardingStatus, OnboardingStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/connections", tags=["databases"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    404: {"model": ErrorResponse, "description": "No such database, or no dictionary yet"},
    503: {"model": ErrorResponse, "description": "Onboarding isn't available"},
}
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    409: {"model": ErrorResponse, "description": "A run is going on, or no LLM key saved"},
}


class OnboardingInfo(BaseModel):
    """How far a database's onboarding is."""

    status: OnboardingStatus
    tables_total: int = Field(description="Tables this run describes")
    tables_drafted: int
    tables_skipped: int = Field(
        description="Not described: names that aren't plain lowercase, over the table cap, or an "
        "answer that didn't fit"
    )
    error: str | None = Field(description="Why it failed, in words to show")
    generated_by: str = Field(description="The LLM provider that drafted it")
    has_dictionary: bool
    started_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, record: OnboardingRecord) -> "OnboardingInfo":
        return cls(
            status=record.status,
            tables_total=record.tables_total,
            tables_drafted=record.tables_drafted,
            tables_skipped=record.tables_skipped,
            error=record.error,
            generated_by=record.generated_by,
            has_dictionary=record.dictionary_version > 0,
            started_at=record.started_at,
            updated_at=record.updated_at,
        )


class TableEdit(BaseModel):
    """One table's reviewed description."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", max_length=500)
    columns: dict[str, Annotated[str, Field(max_length=300)]] = {}
    reviewed: bool = False


class DictionaryEdit(BaseModel):
    """An admin's corrections: only tables (and columns) the dictionary already has."""

    model_config = ConfigDict(extra="forbid")

    tables: dict[str, TableEdit] = Field(max_length=2000)


def get_onboarding(request: Request) -> OnboardingStore:
    """The onboarding store created at startup.

    Raises:
        StoreUnavailableError: the app was started without it (HTTP 503).
    """
    store: OnboardingStore | None = getattr(request.app.state, "onboarding", None)
    if store is None:
        raise StoreUnavailableError("Onboarding isn't available on this server.")
    return store


def get_onboarder(request: Request) -> Onboarder | None:
    """What runs onboardings in the background, or None (tests that don't run any)."""
    onboarder: Onboarder | None = getattr(request.app.state, "onboarder", None)
    return onboarder


Onboarding = Annotated[OnboardingStore, Depends(get_onboarding)]
Runner = Annotated[Onboarder | None, Depends(get_onboarder)]
Llms = Annotated[WorkspaceLLMs | None, Depends(get_workspace_llms)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]
Connections = Annotated[ConnectionStore, Depends(get_connections)]


def start_onboarding(
    onboarder: Onboarder | None,
    llms: WorkspaceLLMs | None,
    user: AuthUser,
    connection_id: UUID,
    audit: AuditStore | None,
) -> OnboardingRecord:
    """Queue a run on the workspace's own key.

    Raises:
        OwnKeyRequiredError: no LLM key saved (409).
        OnboardingRunningError: one is already going on (409).
    """
    if onboarder is None:
        raise StoreUnavailableError("Onboarding isn't available on this server.")
    if llms is None or llms.provider_for(user.workspace_id) is None:
        raise OwnKeyRequiredError(
            "Onboarding drafts the data dictionary with your own LLM key: an admin can save one "
            "in Settings."
        )
    record = onboarder.start(user.workspace_id, connection_id)
    _write(audit, user, "onboarding_started", connection_id)
    return record


def start_if_possible(
    onboarder: Onboarder | None,
    llms: WorkspaceLLMs | None,
    user: AuthUser,
    connection_id: UUID,
    audit: AuditStore | None,
) -> None:
    """After saving a database: onboard it at once when the workspace has its own key. Never
    fails the save: without a key, the admin starts it later."""
    try:
        start_onboarding(onboarder, llms, user, connection_id, audit)
    except (OwnKeyRequiredError, StoreError) as exc:
        logger.info("onboarding not started", extra={"reason": type(exc).__name__})


@router.post(
    "/{connection_id}/onboard",
    response_model=OnboardingInfo,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_ADMIN_ERRORS,
        429: {"model": ErrorResponse, "description": "Too many checks: wait Retry-After"},
    },
    dependencies=[Depends(limit_connection_checks)],
)
def onboard(
    connection_id: UUID,
    user: AdminUser,
    connections: Connections,
    onboarder: Runner,
    llms: Llms,
    audit: Audit,
) -> OnboardingInfo:
    """Read the database's tables and draft their descriptions with the workspace's own key, in
    the background. Tables already described or reviewed are kept: a new run fills the gaps."""
    connections.get(user.workspace_id, connection_id)  # 404 unless it's this workspace's
    return OnboardingInfo.of(start_onboarding(onboarder, llms, user, connection_id, audit))


@router.get("/{connection_id}/onboarding", response_model=OnboardingInfo, responses=_ERRORS)
def get_onboarding_status(
    connection_id: UUID, user: CurrentUser, onboarding: Onboarding
) -> OnboardingInfo:
    """Where the database's onboarding is. Poll it while it is queued, reading or drafting."""
    record = onboarding.get(user.workspace_id, connection_id)
    if record is None:
        raise DictionaryNotFoundError("This database hasn't been onboarded yet.")
    return OnboardingInfo.of(record)


@router.get("/{connection_id}/dictionary", response_model=DataDictionary, responses=_ERRORS)
def get_dictionary(
    connection_id: UUID, user: CurrentUser, onboarding: Onboarding
) -> DataDictionary:
    """The drafted descriptions of the database's tables and columns, for review."""
    record = onboarding.get(user.workspace_id, connection_id)
    if record is None or record.dictionary_version == 0:
        raise DictionaryNotFoundError("This database has no data dictionary yet: onboard it.")
    return record.dictionary


@router.put("/{connection_id}/dictionary", response_model=DataDictionary, responses=_ADMIN_ERRORS)
def edit_dictionary(
    connection_id: UUID,
    edit: DictionaryEdit,
    user: AdminUser,
    onboarding: Onboarding,
    pipelines: Annotated[PipelineRegistry | None, Depends(get_pipelines)],
    audit: Audit,
) -> DataDictionary:
    """Correct descriptions and mark tables reviewed (a reviewed table is never redrafted).
    Tables left out of the request keep what they had."""
    record = onboarding.get(user.workspace_id, connection_id)
    if record is None or record.dictionary_version == 0:
        raise DictionaryNotFoundError("This database has no data dictionary yet: onboard it.")
    edited = apply_edit(record.dictionary, edit)
    saved = onboarding.edit(user.workspace_id, connection_id, edited)
    if pipelines is not None:
        pipelines.forget(connection_id)  # the next question sees the new descriptions
    _write(audit, user, "dictionary_edited", connection_id)
    return saved.dictionary


def apply_edit(dictionary: DataDictionary, edit: DictionaryEdit) -> DataDictionary:
    """The dictionary with the edit applied.

    Raises:
        DictionaryEditError: a table or column the dictionary doesn't have (422).
    """
    tables = dict(dictionary.tables)
    for name, change in edit.tables.items():
        current = tables.get(name)
        if current is None:
            raise DictionaryEditError(f"The dictionary has no table {name!r}.")
        unknown = sorted(set(change.columns) - set(current.columns))
        if unknown:
            raise DictionaryEditError(f"Table {name!r} has no column {unknown[0]!r}.")
        tables[name] = DictionaryTable(
            reviewed=change.reviewed,
            description=change.description.strip(),
            columns={
                column: change.columns.get(column, text).strip()
                for column, text in current.columns.items()
            },
        )
    return dictionary.model_copy(update={"tables": tables})


def _write(audit: AuditStore | None, user: AuthUser, action: Action, connection_id: UUID) -> None:
    audit_trail.write(
        audit,
        AuditEntry(
            action=action,
            outcome="ok",
            user_id=user.user_id,
            workspace_id=user.workspace_id,
            email=user.email,
            connection_id=connection_id,
        ),
    )
