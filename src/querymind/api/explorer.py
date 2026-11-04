"""The endpoints for the data explorer: the tables, and what they mean. Metadata only, no values.

`/semantic` reads only files, so it still works with the database down.
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
from querymind.api.schemas import ErrorResponse, TableQuestionList
from querymind.config import Settings
from querymind.db.connection import connect_readonly
from querymind.db.errors import DatabaseUnavailableError
from querymind.db.schema import SchemaInfo, introspect_schema, read_row_counts
from querymind.semantic.catalog import Catalog, SemanticOverview, build_catalog, build_overview
from querymind.semantic.dictionary import load_dictionaries
from querymind.semantic.examples import load_all_examples
from querymind.semantic.loader import load_semantic_layers

router = APIRouter(tags=["explorer"])

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
        self._overview = build_overview(
            self._layers, load_dictionaries(layer_paths), load_all_examples(layer_paths)
        )
        self._read_schema = read_schema
        self._catalog: Catalog | None = None
        self._lock = threading.Lock()

    def overview(self) -> SemanticOverview:
        return self._overview

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
def get_catalog(explorer: Explorer) -> Catalog:
    """Every table with its columns, keys, business and approximate row count."""
    return explorer.catalog()


@router.get(
    "/catalog/tables/{table}/questions",
    response_model=TableQuestionList,
    responses={503: {"model": ErrorResponse, "description": "History isn't available"}},
)
def get_table_questions(
    table: Annotated[str, PathParam(max_length=63)],  # Postgres's longest name
    store: Store,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
) -> TableQuestionList:
    """Recent saved questions whose SQL read this table. No such table: an empty list."""
    return TableQuestionList(questions=store.recent_questions_using(table, limit=limit))


@router.get("/semantic", response_model=SemanticOverview)
def get_semantic(explorer: Explorer) -> SemanticOverview:
    """Per business: what the tables and columns mean, the terms and the preferred joins."""
    return explorer.overview()
