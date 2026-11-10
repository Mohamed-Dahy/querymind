"""Bring your own database (5.9, ADR 012): save, list, re-test and delete a workspace's databases.

    POST   /connections             save one (admins): connect, check read-only, then save
    GET    /connections             the workspace's databases (never a password)
    POST   /connections/{id}/test   connect and check it again (admins)
    DELETE /connections/{id}        remove it and its password (admins)

Nothing is saved before `db/remote.py` has connected (SSRF-checked address, TLS) and found the
login read-only. Saving and testing are rate-limited per user: each one makes our server connect
to an address a user typed. Asking questions on a saved database comes with 5.11.
"""

import time
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from querymind.agent.registry import PipelineRegistry
from querymind.api import audit_trail
from querymind.api.deps import (
    AdminUser,
    CurrentUser,
    get_audit,
    get_pipelines,
    limit_connection_checks,
)
from querymind.api.errors import error_response
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import AuthUser
from querymind.db.errors import RemoteDatabaseError
from querymind.db.remote import (
    HOST_PATTERN,
    NAME_PATTERN,
    ConnectionSpec,
    SslMode,
    check_read_only,
    connect_remote,
)
from querymind.store.audit import Action, AuditEntry, AuditStore
from querymind.store.connections import ConnectionRecord, ConnectionStore
from querymind.store.errors import StoreUnavailableError

router = APIRouter(prefix="/connections", tags=["databases"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    503: {"model": ErrorResponse, "description": "Saved databases aren't available"},
}
_ADMIN_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ERRORS,
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    404: {"model": ErrorResponse, "description": "No such database in this workspace"},
}
_CHECK_ERRORS: dict[int | str, dict[str, Any]] = {
    **_ADMIN_ERRORS,
    422: {
        "model": ErrorResponse,
        "description": "Refused: an address, port or TLS mode not allowed, no connection, a "
        "refused login, or a login that can change data",
    },
    429: {"model": ErrorResponse, "description": "Too many checks: wait Retry-After"},
}


class SaveConnectionRequest(BaseModel):
    """What an admin sends to add a database."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100, examples=["Sales"])
    host: str = Field(pattern=HOST_PATTERN, examples=["db.example.com"])
    port: int = Field(default=5432, ge=1, le=65535)
    dbname: str = Field(pattern=NAME_PATTERN, examples=["sales"])
    user: str = Field(pattern=NAME_PATTERN, examples=["querymind_reader"])
    # SecretStr: never printed or logged. 1024: generous for any real password.
    password: SecretStr = Field(min_length=1, max_length=1024)
    sslmode: SslMode = "verify-full"

    def spec(self) -> ConnectionSpec:
        return ConnectionSpec(
            host=self.host, port=self.port, dbname=self.dbname, user=self.user, sslmode=self.sslmode
        )


class ConnectionInfo(BaseModel):
    """A saved database, as the API shows it. There is no field for the password."""

    id: UUID
    name: str
    host: str
    port: int
    dbname: str
    user: str
    sslmode: SslMode
    checked_at: datetime = Field(description="When it was last found reachable and read-only")
    created_at: datetime

    @classmethod
    def of(cls, record: ConnectionRecord) -> "ConnectionInfo":
        return cls(
            id=record.id,
            name=record.name,
            host=record.host,
            port=record.port,
            dbname=record.dbname,
            user=record.username,
            sslmode=record.sslmode,
            checked_at=record.checked_at,
            created_at=record.created_at,
        )


def get_connections(request: Request) -> ConnectionStore:
    """The workspaces' databases, created at startup.

    Raises:
        StoreUnavailableError: the app was started without them (HTTP 503).
    """
    store: ConnectionStore | None = request.app.state.connections
    if store is None:
        raise StoreUnavailableError("Saved databases aren't available on this server.")
    return store


def allow_private(request: Request) -> bool:
    """ALLOW_PRIVATE_DATABASES (development and tests only): never settable through the API."""
    return bool(getattr(request.app.state, "allow_private_databases", False))


Connections = Annotated[ConnectionStore, Depends(get_connections)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]
AllowPrivate = Annotated[bool, Depends(allow_private)]
Pipelines = Annotated[PipelineRegistry | None, Depends(get_pipelines)]


def _check(spec: ConnectionSpec, password: SecretStr, *, allow_private: bool) -> None:
    """Connect (SSRF guard, TLS) and refuse a login that can change anything."""
    with connect_remote(spec, password, allow_private=allow_private) as conn:
        check_read_only(conn)


@router.get("", response_model=list[ConnectionInfo], responses=_ERRORS)
def list_connections(user: CurrentUser, store: Connections) -> list[ConnectionInfo]:
    """The workspace's own databases, by name. Passwords are never shown."""
    return [ConnectionInfo.of(record) for record in store.list(user.workspace_id)]


@router.post(
    "",
    response_model=ConnectionInfo,
    status_code=status.HTTP_201_CREATED,
    responses={**_CHECK_ERRORS, 409: {"model": ErrorResponse, "description": "Name taken"}},
    dependencies=[Depends(limit_connection_checks)],
)
def save_connection(
    request: SaveConnectionRequest,
    user: AdminUser,
    store: Connections,
    audit: Audit,
    private: AllowPrivate,
) -> ConnectionInfo:
    """Add a database: QueryMind connects, checks the login can only read, then saves it.

    The address must be public (no private or internal addresses), the port a PostgreSQL one,
    the connection encrypted. A login that could change anything is refused, with how to fix it.
    """
    started = time.perf_counter()
    spec = request.spec()
    try:
        _check(spec, request.password, allow_private=private)
    except RemoteDatabaseError as exc:
        _write(audit, user, "connection_refused", exc, spec, started)
        raise
    record = store.save(user.workspace_id, request.name, spec, request.password)
    _write(audit, user, "connection_saved", None, spec, started)
    return ConnectionInfo.of(record)


@router.post(
    "/{connection_id}/test",
    response_model=ConnectionInfo,
    responses=_CHECK_ERRORS,
    dependencies=[Depends(limit_connection_checks)],
)
def test_connection(
    connection_id: UUID,
    user: AdminUser,
    store: Connections,
    audit: Audit,
    private: AllowPrivate,
    pipelines: Pipelines,
) -> ConnectionInfo:
    """Connect again and check the login is still read-only (grants can change after saving)."""
    started = time.perf_counter()
    record = store.get(user.workspace_id, connection_id)
    spec = record.spec()
    try:
        _check(spec, store.password(user.workspace_id, record), allow_private=private)
    except RemoteDatabaseError as exc:
        _write(audit, user, "connection_refused", exc, spec, started)
        raise
    checked = store.mark_checked(user.workspace_id, connection_id)
    if pipelines is not None:
        pipelines.forget(connection_id)  # its tables may have changed: read them again
    _write(audit, user, "connection_tested", None, spec, started)
    return ConnectionInfo.of(checked)


@router.delete(
    "/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    responses=_ADMIN_ERRORS,
)
def delete_connection(
    connection_id: UUID, user: AdminUser, store: Connections, audit: Audit, pipelines: Pipelines
) -> Response:
    """Remove the database from the workspace, with its password."""
    started = time.perf_counter()
    record = store.get(user.workspace_id, connection_id)
    store.delete(user.workspace_id, connection_id)  # its conversations go with it (cascade)
    if pipelines is not None:
        pipelines.forget(connection_id)  # and its pipeline, with its password, from memory
    _write(audit, user, "connection_deleted", None, record.spec(), started)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _write(
    audit: AuditStore | None,
    user: AuthUser,
    action: Action,
    error: RemoteDatabaseError | None,
    spec: ConnectionSpec,
    started: float,
) -> None:
    """The audit line: who, which database (never the password), and why it was refused."""
    where = f"{spec.user}@{spec.host}:{spec.port}/{spec.dbname}"
    audit_trail.write(
        audit,
        AuditEntry(
            action=action,
            outcome=error_response(error)[1].error if error else "ok",  # e.g. connection_writable
            user_id=user.user_id,
            workspace_id=user.workspace_id,
            email=user.email,
            detail=f"{where}: {error}" if error else where,
            duration_ms=audit_trail.elapsed_ms(started),
        ),
    )
