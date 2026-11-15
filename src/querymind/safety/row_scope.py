"""Row-level security (5.3): which rows a user may see, enforced by Postgres itself.

    scope = ("Eastern",)                        # from the user's membership; None = every row
    execute_query(conn, query, timeout_seconds=30, row_scope=scope)
    # inside the query's transaction: SELECT set_config('querymind.scope', 'Eastern', true)

Table permissions (5.2) and PII masking (5.4) are enforced by our code. Row-level security is
enforced by the database: on Northwind, `data/seed_northwind.py` puts a policy on `orders` (and on
`order_details`, through its order) that reads the session setting `querymind.scope`:
- `*`: every row (an admin, or a user with no scope; evals and scripts);
- `Eastern,Western`: only the orders taken by an employee of one of those sales regions;
- not set, empty or unknown: no row at all (fail closed).
The executor sets it in every query's transaction (`SET LOCAL`, through `set_config(..., true)`),
so it can't outlive the query, and the LLM's SQL can't change it: `set_config` and
`current_setting` aren't on the function allowlist, and SET is refused by the validator.

A user's scope is a list of tags on their membership (store/row_scope.py), set by an admin. The same
tags go to every database: on the demo they are Northwind's sales regions; on a user's own database
(5.11) their own policies may read `querymind.scope` the same way (we create nothing there).

Limit: the schema the LLM sees is read once for everyone, with every row. So a scoped user's prompt
gets no sample rows from tables with row security (`without_row_samples`), and the explorer hides
those tables' row counts.
"""

from collections.abc import Iterable

from querymind.db.schema import SchemaInfo

SCOPE_SETTING = "querymind.scope"  # the session setting the policies read
EVERY_ROW = "*"
SEPARATOR = ","
MAX_TAGS = 50
MAX_TAG_LENGTH = 100

# None = every row. Otherwise the tags of the rows this user may see; `()` = no row at all (only
# for a user who is no longer a member: a stored scope is never empty, see `checked`).
type RowScope = tuple[str, ...] | None


class RowScopeInvalidError(ValueError):
    """A scope that can't be stored: an empty tag, a reserved character, too many. Safe to show."""


def scope_value(scope: RowScope) -> str:
    """What `querymind.scope` is set to: `*` for every row, else the tags joined by commas."""
    return EVERY_ROW if scope is None else SEPARATOR.join(scope)


def checked(tags: Iterable[str]) -> tuple[str, ...]:
    """Tags as stored: trimmed, once each, sorted.

    Raises:
        RowScopeInvalidError: none at all (use null for every row), an empty tag, a tag with
            `,` (the separator) or `*` (every row), or too many / too long.
    """
    cleaned = sorted({tag.strip() for tag in tags})
    if not cleaned:
        raise RowScopeInvalidError("Give at least one value, or null for every row.")
    if len(cleaned) > MAX_TAGS:
        raise RowScopeInvalidError(f"At most {MAX_TAGS} values.")
    for tag in cleaned:
        if not tag:
            raise RowScopeInvalidError("A value can't be empty.")
        if SEPARATOR in tag or EVERY_ROW in tag:
            raise RowScopeInvalidError(f"A value can't contain {SEPARATOR!r} or {EVERY_ROW!r}.")
        if len(tag) > MAX_TAG_LENGTH:
            raise RowScopeInvalidError(f"A value can be at most {MAX_TAG_LENGTH} characters.")
    return tuple(cleaned)


def without_row_samples(schema: SchemaInfo) -> SchemaInfo:
    """The schema without the sample rows of tables with row security: they were read with every
    row, so a scoped user's LLM could quote another region's order."""
    return SchemaInfo(
        tables=tuple(
            table.model_copy(update={"sample_rows": ()}) if table.row_security else table
            for table in schema.tables
        )
    )
