"""The endpoints for the data explorer: the tables, and what they mean. Metadata only, no values.

`/semantic` reads only files, so it still works with the database down.

A workspace's own database (U7.1b): `?connection_id=` on each route. Its tables come from its
pipeline (the same schema read its first question does) and its meanings from its onboarded
dictionary; no LLM key is needed to look, only to ask. Another workspace's id is a 404, like an id
that doesn't exist. Row counts there are Postgres's estimates only: no count(*) on someone's server.

Table permissions (5.2): a role restricted on the demo database sees only its allowed tables here,
with no key, term, join or suggested question that names another. A hidden table's saved questions
come back empty, exactly like a table that doesn't exist.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Protocol
from uuid import UUID
from weakref import WeakKeyDictionary

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam

from querymind.api.conversations import Store
from querymind.api.deps import (
    CurrentUser,
    RowScopes,
    TableAccess,
    allowed_tables,
    get_connections,
    get_current_user,
    get_pipelines,
    owner_of,
    row_scope_of,
)
from querymind.api.schemas import ErrorResponse, TableQuestionList
from querymind.auth.tokens import AuthUser
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SchemaInfo, introspect_schema, read_row_counts
from querymind.safety.permissions import visible_catalog, visible_examples, visible_layers
from querymind.semantic.catalog import Catalog, SemanticOverview, build_catalog, build_overview
from querymind.semantic.dictionary import DataDictionary, load_dictionaries, personal_columns
from querymind.semantic.examples import load_all_examples
from querymind.semantic.loader import load_semantic_layers
from querymind.semantic.models import SemanticLayer
from querymind.store.errors import StoreUnavailableError

# Every route here shows the schema or saved questions: logged-in users only (5.1), and only the
# tables their role may see (5.2).
router = APIRouter(tags=["explorer"], dependencies=[Depends(get_current_user)])

_DATABASE_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No such database in this workspace"},
    409: {"model": ErrorResponse, "description": "The saved database's password can't be read"},
    422: {"model": ErrorResponse, "description": "The saved database can't be reached"},
    503: {"model": ErrorResponse, "description": "The database isn't available"},
}

# `?connection_id=`: which database (U7.1b). Left out: the demo.
DatabaseId = Annotated[
    UUID | None,
    Query(description="One of this workspace's saved databases; left out = the demo"),
]

ReadSchema = Callable[[], tuple[SchemaInfo, Mapping[str, int | None]]]


class ExplorerData:
    """Both responses, built once and reused. Restart the app after changing the database.

    A failed read isn't kept: the next request tries again, so the catalog comes back by itself
    once the database does.
    """

    def __init__(self, layer_paths: Sequence[Path], read_schema: ReadSchema) -> None:
        self._layers = load_semantic_layers(layer_paths)
        self._dictionaries = load_dictionaries(layer_paths)
        self._examples = load_all_examples(layer_paths)
        self._overview = build_overview(self._layers, self._dictionaries, self._examples)
        self._read_schema = read_schema
        self._catalog: Catalog | None = None
        self._secured: frozenset[str] = frozenset()  # tables with row-level security (5.3)
        self._lock = threading.Lock()

    def overview(self, allowed: frozenset[str] | None = None) -> SemanticOverview:
        """What the tables mean; with `allowed` (5.2), only those tables and what stays within them.
        Examples on hidden tables are dropped first, so they aren't logged as unmatched."""
        if allowed is None:
            return self._overview
        return build_overview(
            visible_layers(self._layers, allowed),
            self._dictionaries,
            visible_examples(self._examples, allowed),
        )

    def visible_catalog(self, allowed: frozenset[str] | None, *, scoped: bool = False) -> Catalog:
        """The catalog as this role and user see it (`_as_seen`)."""
        catalog = self.catalog()
        return _as_seen(catalog, self._secured, allowed, scoped=scoped)

    def catalog(self) -> Catalog:
        with self._lock:
            if self._catalog is None:
                schema, row_counts = self._read_schema()
                self._catalog = build_catalog(schema, row_counts, self._layers)
                self._secured = frozenset(t.name for t in schema.tables if t.row_security)
            return self._catalog


def _as_seen(
    catalog: Catalog, secured: frozenset[str], allowed: frozenset[str] | None, *, scoped: bool
) -> Catalog:
    """The catalog as a role sees it (5.2): every table, or only the allowed ones. For a user who
    sees only some rows (5.3), the row counts of row-secured tables are hidden: they were counted
    for everyone, so "830 orders" would tell an Eastern manager about the others."""
    seen = catalog if allowed is None else visible_catalog(catalog, allowed)
    if not scoped:
        return seen
    return Catalog(
        tables=[
            table.model_copy(update={"row_count": None}) if table.name in secured else table
            for table in seen.tables
        ]
    )


class DescribesDatabase(Protocol):
    """What the explorer needs from a saved database's pipeline (agent/pipeline.py)."""

    def described_schema(
        self,
    ) -> tuple[SchemaInfo, list[SemanticLayer], DataDictionary | None]: ...

    def row_counts(self, *, count_missing: bool) -> dict[str, int | None]: ...


type Personal = frozenset[tuple[str, str]]
type SavedParts = tuple[
    Catalog, frozenset[str], list[SemanticLayer], dict[str, DataDictionary], Personal
]


class SavedDatabaseExplorer:
    """A workspace's own database, for the explorer (U7.1b): the same two answers as ExplorerData,
    from its pipeline. Read on first use and kept with that pipeline (see `explorer_for`); a failed
    read isn't kept, so the next request tries again.

    Its semantic layer is its onboarded dictionary's: none yet (not onboarded) means no business in
    the overview, and no business on the catalog's tables.
    """

    def __init__(self, pipeline: DescribesDatabase) -> None:
        self._pipeline = pipeline
        self._lock = threading.Lock()
        self._read: SavedParts | None = None

    def _parts(self) -> SavedParts:
        with self._lock:
            if self._read is None:
                try:
                    schema, layers, dictionary = self._pipeline.described_schema()
                    counts = self._pipeline.row_counts(count_missing=False)
                except psycopg.Error as exc:
                    raise DatabaseUnavailableError(
                        "Could not read this database's structure. Please try again later."
                    ) from exc
                # Under the layer's name, as build_overview looks it up: the personal-data flags
                # then match what questions mask (5.4).
                dictionaries = (
                    {layer.database: dictionary for layer in layers} if dictionary else {}
                )
                secured = frozenset(t.name for t in schema.tables if t.row_security)
                # Exactly what its questions mask (5.4): the dictionary's flags, or the name
                # guesses for a table not flagged yet. The layer alone doesn't carry them.
                personal = personal_columns(dictionary, schema)
                catalog = build_catalog(schema, counts, layers)
                self._read = (catalog, secured, layers, dictionaries, personal)
            return self._read

    def visible_catalog(self, allowed: frozenset[str] | None, *, scoped: bool = False) -> Catalog:
        catalog, secured, *_ = self._parts()
        return _as_seen(catalog, secured, allowed, scoped=scoped)

    def overview(self, allowed: frozenset[str] | None = None) -> SemanticOverview:
        _, _, layers, dictionaries, personal = self._parts()
        seen = layers if allowed is None else visible_layers(layers, allowed)
        return _with_personal(build_overview(seen, dictionaries, []), personal)


def _with_personal(overview: SemanticOverview, personal: Personal) -> SemanticOverview:
    """Each column's `pii` set from `personal`, so the explorer's badge and the masked `***`
    in an answer never disagree."""
    return overview.model_copy(
        update={
            "businesses": [
                business.model_copy(
                    update={
                        "tables": [
                            table.model_copy(
                                update={
                                    "columns": [
                                        column.model_copy(
                                            update={"pii": (table.name, column.name) in personal}
                                        )
                                        for column in table.columns
                                    ]
                                }
                            )
                            for table in business.tables
                        ]
                    }
                )
                for business in overview.businesses
            ]
        }
    )


# One per pipeline object, so it goes when the registry drops or rebuilds that pipeline (a delete,
# "test again", a new dictionary version): never a stale catalog, and no cache of our own to clear.
_saved_explorers: WeakKeyDictionary[DescribesDatabase, SavedDatabaseExplorer] = WeakKeyDictionary()
_saved_lock = threading.Lock()


def explorer_for(
    request: Request, user: AuthUser, connection_id: UUID | None
) -> "ExplorerData | SavedDatabaseExplorer":
    """The demo's explorer, or this workspace's saved database's.

    Raises:
        ConnectionNotFoundError: not one of this workspace's databases (404).
        ConnectionUnreadableError: its saved password can't be decrypted (409).
        StoreUnavailableError: saved databases aren't available on this server (503).
    """
    if connection_id is None:
        return get_explorer(request)
    pipelines = get_pipelines(request)
    if pipelines is None:
        raise StoreUnavailableError("Saved databases aren't available on this server.")
    pipeline = pipelines.for_database(user.workspace_id, connection_id)
    with _saved_lock:
        found = _saved_explorers.get(pipeline)
        if found is None:
            found = _saved_explorers[pipeline] = SavedDatabaseExplorer(pipeline)
        return found


def read_schema_without_samples(
    settings: Settings,
) -> tuple[SchemaInfo, Mapping[str, int | None]]:
    """The structure and row counts, as the read-only role. `sample_rows=0`: no value is read."""
    try:
        with connect_readonly(settings) as conn:
            schema = introspect_schema(conn, sample_rows=0)
            return schema, read_row_counts(conn, schema.table_names)
    except psycopg.Error as exc:
        raise DatabaseUnavailableError(
            "Could not read the database's structure. Please try again later."
        ) from exc


def create_explorer(settings: Settings) -> ExplorerData:
    return ExplorerData(
        settings.semantic_layer_paths, partial(read_schema_without_samples, settings)
    )


def get_explorer(request: Request) -> ExplorerData:
    """The explorer data created at startup (see main.py), stored on the app."""
    explorer: ExplorerData = request.app.state.explorer
    return explorer


@router.get("/catalog", response_model=Catalog, responses=_DATABASE_ERRORS)
def get_catalog(
    request: Request,
    user: CurrentUser,
    access: TableAccess,
    scopes: RowScopes,
    connection_id: DatabaseId = None,
) -> Catalog:
    """Every table you may use, with its columns, keys, business and approximate row count (not
    for a table whose rows depend on who asks, if you see only some of them, 5.3)."""
    explorer = explorer_for(request, user, connection_id)
    scoped = row_scope_of(scopes, user) is not None
    return explorer.visible_catalog(allowed_tables(access, user, connection_id), scoped=scoped)


@router.get(
    "/catalog/tables/{table}/questions",
    response_model=TableQuestionList,
    responses={
        404: {"model": ErrorResponse, "description": "No such database in this workspace"},
        503: {"model": ErrorResponse, "description": "History isn't available"},
    },
)
def get_table_questions(
    request: Request,
    table: Annotated[str, PathParam(max_length=63)],  # Postgres's longest name
    user: CurrentUser,
    store: Store,
    access: TableAccess,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    connection_id: DatabaseId = None,
) -> TableQuestionList:
    """Your recent saved questions whose SQL read this table, on this database. No such table
    (or one your role may not use, 5.2): an empty list, the same answer, so it can't tell which
    tables exist."""
    if connection_id is not None:
        get_connections(request).get(user.workspace_id, connection_id)  # 404: not this workspace's
    allowed = allowed_tables(access, user, connection_id)
    if allowed is not None and table not in allowed:
        return TableQuestionList(questions=[])
    found = store.recent_questions_using(
        table, owner=owner_of(user), limit=limit, connection_id=connection_id
    )
    return TableQuestionList(questions=found)


@router.get("/semantic", response_model=SemanticOverview, responses=_DATABASE_ERRORS)
def get_semantic(
    request: Request, user: CurrentUser, access: TableAccess, connection_id: DatabaseId = None
) -> SemanticOverview:
    """Per business: what the tables and columns mean, the terms and the preferred joins (only
    the tables you may use). A saved database: its onboarded dictionary (none yet: no business);
    reading it needs the database itself, unlike the demo's files."""
    explorer = explorer_for(request, user, connection_id)
    return explorer.overview(allowed_tables(access, user, connection_id))
