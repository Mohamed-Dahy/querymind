"""The endpoints for the data explorer: the tables, and what they mean. Metadata only, no values.

`/semantic` reads only files, so it still works with the database down.

Table permissions (5.2): a role restricted on the demo database sees only its allowed tables here,
with no key, term, join or suggested question that names another. A hidden table's saved questions
come back empty, exactly like a table that doesn't exist.
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, Query, Request
from fastapi import Path as PathParam

from querymind.api.conversations import Store
from querymind.api.deps import (
    CurrentUser,
    TableAccess,
    allowed_tables,
    get_current_user,
    owner_of,
)
from querymind.api.schemas import ErrorResponse, TableQuestionList
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SchemaInfo, introspect_schema, read_row_counts
from querymind.safety.permissions import visible_catalog, visible_examples, visible_layers
from querymind.semantic.catalog import Catalog, SemanticOverview, build_catalog, build_overview
from querymind.semantic.dictionary import load_dictionaries
from querymind.semantic.examples import load_all_examples
from querymind.semantic.loader import load_semantic_layers

# Every route here shows the schema or saved questions: logged-in users only (5.1), and only the
# tables their role may see (5.2).
router = APIRouter(tags=["explorer"], dependencies=[Depends(get_current_user)])

_DATABASE_ERRORS: dict[int | str, dict[str, Any]] = {
    503: {"model": ErrorResponse, "description": "The database isn't available"},
}

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

    def visible_catalog(self, allowed: frozenset[str] | None) -> Catalog:
        """The catalog as a role sees it (5.2): every table, or only the allowed ones."""
        catalog = self.catalog()
        return catalog if allowed is None else visible_catalog(catalog, allowed)

    def catalog(self) -> Catalog:
        with self._lock:
            if self._catalog is None:
                schema, row_counts = self._read_schema()
                self._catalog = build_catalog(schema, row_counts, self._layers)
            return self._catalog


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


Explorer = Annotated[ExplorerData, Depends(get_explorer)]


@router.get("/catalog", response_model=Catalog, responses=_DATABASE_ERRORS)
def get_catalog(explorer: Explorer, user: CurrentUser, access: TableAccess) -> Catalog:
    """Every table you may use, with its columns, keys, business and approximate row count."""
    return explorer.visible_catalog(allowed_tables(access, user, None))


@router.get(
    "/catalog/tables/{table}/questions",
    response_model=TableQuestionList,
    responses={503: {"model": ErrorResponse, "description": "History isn't available"}},
)
def get_table_questions(
    table: Annotated[str, PathParam(max_length=63)],  # Postgres's longest name
    user: CurrentUser,
    store: Store,
    access: TableAccess,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> TableQuestionList:
    """Your recent saved questions whose SQL read this table. No such table (or one your role may
    not use, 5.2): an empty list, the same answer, so it can't tell which tables exist."""
    allowed = allowed_tables(access, user, None)
    if allowed is not None and table not in allowed:
        return TableQuestionList(questions=[])
    found = store.recent_questions_using(table, owner=owner_of(user), limit=limit)
    return TableQuestionList(questions=found)


@router.get("/semantic", response_model=SemanticOverview)
def get_semantic(explorer: Explorer, user: CurrentUser, access: TableAccess) -> SemanticOverview:
    """Per business: what the tables and columns mean, the terms and the preferred joins (only
    the tables you may use)."""
    return explorer.overview(allowed_tables(access, user, None))
