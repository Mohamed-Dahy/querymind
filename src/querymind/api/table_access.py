"""Table permissions (5.2): which tables the analyst and viewer roles may use, per database.

    GET /table-access?connection_id=…   the database's tables and each role's rule (admins)
    PUT /table-access                   set one role's rule: a list of tables, or null = every table

No `connection_id` means the shared demo database: the rule then holds only in this workspace. A
rule lists what the role may use, so a table added to the database later stays hidden until an
admin grants it. Admins are never restricted (they set the rules). The tables are checked against
the database's real schema: a misspelt name would otherwise hide a table without anyone noticing.
"""

from typing import Annotated, Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from querymind.agent.registry import PipelineRegistry
from querymind.api import audit_trail
from querymind.api.deps import AdminUser, get_audit, get_pipelines
from querymind.api.schemas import ErrorResponse
from querymind.auth.tokens import AuthUser
from querymind.store.audit import AuditEntry, AuditStore
from querymind.store.errors import StoreUnavailableError, TableAccessInvalidError
from querymind.store.table_access import RestrictedRole, TableAccessStore

router = APIRouter(prefix="/table-access", tags=["databases"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not logged in"},
    403: {"model": ErrorResponse, "description": "Not an admin of this workspace"},
    404: {"model": ErrorResponse, "description": "Not one of this workspace's databases"},
    503: {"model": ErrorResponse, "description": "Permissions or the database aren't available"},
}


class ListsTables(Protocol):
    """A database's pipeline: it knows the tables its read-only login can read."""

    def table_names(self) -> list[str]: ...


class TableAccessInfo(BaseModel):
    """A database's tables, and which of them each restrictable role may use."""

    connection_id: UUID | None  # None: the shared demo database
    tables: list[str]  # every table of the database (what a rule can choose from)
    analyst: list[str] | None  # None: every table, now and later
    viewer: list[str] | None


class TableAccessUpdate(BaseModel):
    """One role's rule. `tables: null` lifts it (every table); `[]` leaves the role no table."""

    model_config = ConfigDict(extra="forbid")

    connection_id: UUID | None = None
    role: RestrictedRole
    tables: list[str] | None = Field(default=None, max_length=1000)


def get_table_access_store(request: Request) -> TableAccessStore:
    """The rules store; a server without it can't manage permissions (503)."""
    store: TableAccessStore | None = getattr(request.app.state, "table_access", None)
    if store is None:
        raise StoreUnavailableError("Table permissions aren't available on this server.")
    return store


Store = Annotated[TableAccessStore, Depends(get_table_access_store)]
Registry = Annotated[PipelineRegistry | None, Depends(get_pipelines)]
Audit = Annotated[AuditStore | None, Depends(get_audit)]


def _tables_of(
    registry: PipelineRegistry | None, admin: AuthUser, connection_id: UUID | None
) -> list[str]:
    """Every table of the database (reads its schema once). 404 for another workspace's."""
    if registry is None:
        raise StoreUnavailableError("Databases aren't available on this server.")
    pipeline: ListsTables = registry.for_database(admin.workspace_id, connection_id)
    return pipeline.table_names()


@router.get("", response_model=TableAccessInfo, responses=_ERRORS)
def get_table_access(
    admin: AdminUser,
    store: Store,
    registry: Registry,
    connection_id: Annotated[UUID | None, Query()] = None,
) -> TableAccessInfo:
    """The database's tables and each role's rule. Admins only."""
    tables = _tables_of(registry, admin, connection_id)
    rules = store.rules(admin.workspace_id, connection_id)
    return TableAccessInfo(
        connection_id=connection_id, tables=tables, analyst=rules["analyst"], viewer=rules["viewer"]
    )


@router.put(
    "",
    response_model=TableAccessInfo,
    responses={**_ERRORS, 422: {"model": ErrorResponse, "description": "A table it doesn't have"}},
)
def put_table_access(
    update: TableAccessUpdate,
    admin: AdminUser,
    store: Store,
    registry: Registry,
    audit: Audit,
) -> TableAccessInfo:
    """Set which tables a role may use on a database (null = every table). Admins only; audited."""
    tables = _tables_of(registry, admin, update.connection_id)
    if update.tables is not None:
        unknown = sorted(set(update.tables) - set(tables))
        if unknown:
            raise TableAccessInvalidError(
                f"This database has no table called {', '.join(unknown)}."
            )
    store.set(admin.workspace_id, update.connection_id, update.role, update.tables)
    audit_trail.write(
        audit,
        AuditEntry(
            action="table_access_changed",
            outcome="ok",
            user_id=admin.user_id,
            workspace_id=admin.workspace_id,
            email=admin.email,
            connection_id=update.connection_id,
            tables=tuple(sorted(set(update.tables))) if update.tables is not None else (),
            detail=f"{update.role}: "
            + ("every table" if update.tables is None else f"{len(set(update.tables))} tables"),
        ),
    )
    rules = store.rules(admin.workspace_id, update.connection_id)
    return TableAccessInfo(
        connection_id=update.connection_id,
        tables=tables,
        analyst=rules["analyst"],
        viewer=rules["viewer"],
    )
