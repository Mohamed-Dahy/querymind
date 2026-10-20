"""The SQL safety gate: every query the LLM writes must pass `validate_sql` before it runs.

    query = validate_sql(draft.sql, allowed_tables=schema.table_names, max_rows=1000)
    query.sql       # safe SQL, with a LIMIT, ready for the executor
    query.tables    # the real tables it reads
    query.max_rows  # the row cap the executor will apply

How it works: sqlglot parses the SQL into a tree (like a sentence diagram), and we check the
tree, not the text. Text checks ("does it contain DELETE?") are easy to fool with comments,
quoting or odd casing; the tree shows what the query really does.

Checks, in order (the first failure raises UnsafeSqlError):
  1. It parses, and is exactly one statement.
  2. That statement is a SELECT (WITH ... SELECT and UNION/INTERSECT/EXCEPT included).
  3. Nothing inside it writes, locks or changes settings (DML in a CTE, SELECT INTO, FOR UPDATE).
  4. Every table is an allowed table in the public schema (so no system catalogs).
  5. Every function is on the allowlist (safety/allowed_functions.py).
  6. The outer query returns at most `max_rows + 1` rows: a LIMIT is added, or lowered if too
     high. The one extra row lets the executor tell "exactly max_rows rows" apart from "more
     rows existed and were cut off" (it shows max_rows and reports `truncated`).

The SQL that comes out is regenerated from the checked tree, so what runs is exactly what was
checked, never a different reading of the original text.

This is one layer of several: the read-only role, statement timeout and row limit still apply
even if a query got past this validator.
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

from querymind.safety.allowed_functions import ALLOWED_ANONYMOUS_FUNCTIONS, ALLOWED_FUNCTION_TYPES
from querymind.safety.errors import UnsafeSqlError

logger = logging.getLogger(__name__)

# Longer input is rejected before parsing: real questions need far less, and a huge input
# could make parsing itself slow.
MAX_SQL_CHARS = 20_000

ALLOWED_SCHEMA = "public"

# Statements and clauses that write data, change the database or its settings, or lock rows.
# Checked on every node of the tree, so they are caught even inside a CTE or subquery.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Into,  # SELECT ... INTO new_table creates a table
    exp.Lock,  # SELECT ... FOR UPDATE / FOR SHARE locks rows
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Copy,
    exp.Grant,
    exp.Revoke,
    exp.Set,
    exp.Command,  # anything sqlglot could not fully parse
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
)

# Ensures only validate_sql can build a ValidatedQuery (see __post_init__).
_VALIDATOR_KEY = object()


@dataclass(frozen=True)
class ValidatedQuery:
    """SQL that passed every check. The executor only accepts this type, never a plain string,
    so "run SQL that skipped the validator" is a type error, not just a code-review comment.

    Create it only with `validate_sql()`; constructing it directly raises TypeError. This guards
    against mistakes, not against deliberate tricks in our own code (`dataclasses.replace` would
    copy the key): Python has no true private constructors. Real attackers only control the SQL
    text, and that always goes through the checks.
    """

    sql: str
    tables: frozenset[str]
    max_rows: int  # carried along, so the executor caps rows with the same number
    _key: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._key is not _VALIDATOR_KEY:
            raise TypeError("ValidatedQuery can only be created by validate_sql()")


def validate_sql(sql: str, *, allowed_tables: Collection[str], max_rows: int) -> ValidatedQuery:
    """Check `sql` and return a safe, row-limited version of it.

    Args:
        sql: the query to check (usually written by the LLM).
        allowed_tables: tables the user may read, e.g. `SchemaInfo.table_names`.
        max_rows: the most rows the user may get back (the SQL allows one more, see check 6).

    Raises:
        UnsafeSqlError: with a plain-language reason, at the first check that fails.
    """
    try:
        query = _parse_single_select(sql)
        _reject_forbidden_nodes(query)
        tables = _check_tables(query, allowed_tables)
        _check_functions(query)
        _enforce_row_limit(query, max_rows + 1)
    except UnsafeSqlError as exc:
        logger.warning("unsafe sql rejected", extra={"reason": exc.reason, "sql": sql})
        raise

    # comments=False: comments are dropped; they can't change a query, only hide things.
    safe_sql = query.sql(dialect="postgres", comments=False)
    return ValidatedQuery(
        sql=safe_sql, tables=frozenset(tables), max_rows=max_rows, _key=_VALIDATOR_KEY
    )


def _parse_single_select(sql: str) -> exp.Query:
    """Checks 1 and 2: exactly one statement, and it is a SELECT (or UNION etc. of SELECTs)."""
    if not sql.strip():
        raise UnsafeSqlError("The query is empty.")
    if len(sql) > MAX_SQL_CHARS:
        raise UnsafeSqlError(f"The query is too long (over {MAX_SQL_CHARS} characters).")
    try:
        parsed = sqlglot.parse(sql, read="postgres")
    except (SqlglotError, RecursionError) as exc:
        # Fail closed: SQL we can't understand is SQL we can't prove safe.
        raise UnsafeSqlError("The query could not be parsed as PostgreSQL.") from exc

    # A trailing ";" or comment parses as an empty entry; it is not a statement.
    statements = [s for s in parsed if s is not None and not isinstance(s, exp.Semicolon)]
    if not statements:
        raise UnsafeSqlError("The query is empty.")
    if len(statements) > 1:
        raise UnsafeSqlError("Only one statement is allowed.")

    [statement] = statements
    # exp.Select covers WITH ... SELECT too (the WITH is part of the Select node).
    if not isinstance(statement, exp.Select | exp.SetOperation):
        raise UnsafeSqlError("Only SELECT queries are allowed.")
    return statement


def _reject_forbidden_nodes(query: exp.Query) -> None:
    """Check 3: walk every node, so writes hidden inside a CTE or subquery are found too."""
    for node in query.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise UnsafeSqlError(f"The query may only read data ({_describe_node(node)} found).")


def _check_tables(query: exp.Query, allowed_tables: Collection[str]) -> set[str]:
    """Check 4: every table is allowed and in the public schema. Returns the tables read."""
    tables: set[str] = set()
    for table in query.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            # A function used as a table, e.g. FROM generate_series(...): check 5 covers it.
            continue
        name = _identifier_name(table.this)
        schema = table.args.get("db")
        if table.args.get("catalog") is not None or (
            schema is not None and _identifier_name(schema) != ALLOWED_SCHEMA
        ):
            raise UnsafeSqlError(f"Table {table.sql(dialect='postgres')} is not allowed.")
        if schema is None and name in _visible_cte_names(table):
            continue  # a WITH result inside this query, not a real table
        if name not in allowed_tables:
            raise UnsafeSqlError(f"Table {name} is not allowed.")
        tables.add(name)
    return tables


def _visible_cte_names(table: exp.Table) -> set[str]:
    """CTE names Postgres would resolve at this table reference, following its scoping rules.

    Collecting every CTE name in the query would be a bypass: in
    `WITH pg_class AS (SELECT * FROM pg_class) SELECT ...` the inner pg_class is the *real*
    system catalog (a non-recursive CTE can't see itself), and a CTE defined inside a subquery
    is invisible outside it. So we walk up from the table, scope by scope:
      - inside a CTE body: earlier CTEs of the same WITH are visible (all of them, itself
        included, if it is WITH RECURSIVE);
      - inside a query's main body: all CTEs of that query's WITH are visible.
    """
    names: set[str] = set()
    child: exp.Expr = table  # Expr: sqlglot's base node type, what `.parent` returns
    parent = table.parent
    while parent is not None:
        if isinstance(parent, exp.With) and isinstance(child, exp.CTE):
            ctes = parent.expressions
            position = next(i for i, cte in enumerate(ctes) if cte is child)
            visible = ctes if parent.args.get("recursive") else ctes[:position]
            names.update(_cte_name(cte) for cte in visible)
        elif isinstance(parent, exp.Query):
            with_ = parent.args.get("with_")
            if with_ is not None and child is not with_:
                names.update(_cte_name(cte) for cte in with_.expressions)
        child, parent = parent, parent.parent
    return names


def _cte_name(cte: exp.CTE) -> str:
    return _identifier_name(cte.args["alias"].this)


def _check_functions(query: exp.Query) -> None:
    """Check 5: every function call is on the allowlist, and called without a schema name."""
    for func in query.find_all(exp.Func):
        parent = func.parent
        if isinstance(parent, exp.Dot) and parent.expression is func:
            # `other_schema.age(x)` is a different function that only shares an allowed name
            # (sqlglot keeps just "age"). Unqualified names resolve to Postgres's built-ins.
            schema = parent.this.sql(dialect="postgres")
            raise UnsafeSqlError(
                f"Function {schema}.{func.name} is not allowed: call functions without a schema."
            )
        if isinstance(func, exp.Anonymous):
            allowed = func.name.lower() in ALLOWED_ANONYMOUS_FUNCTIONS
        else:
            # Exact class, not isinstance: a subclass of an allowed function is a different
            # function and must be allowed on its own.
            allowed = type(func) in ALLOWED_FUNCTION_TYPES
        if not allowed:
            raise UnsafeSqlError(f"Function {_function_name(func)} is not allowed.")


def _enforce_row_limit(query: exp.Query, limit: int) -> None:
    """Check 6: the outer query gets LIMIT `limit` unless it already has a smaller one.

    Only the outer LIMIT counts: a LIMIT inside a subquery does not limit the result.
    """
    node = query.args.get("limit")
    if isinstance(node, exp.Fetch) and _fetch_option(node, "with_ties", "percent"):
        # WITH TIES can return any number of tied rows, so no row cap could be guaranteed.
        raise UnsafeSqlError("FETCH ... WITH TIES / PERCENT is not supported; use LIMIT.")
    current = _literal_row_limit(node)
    if current is None or current > limit:
        query.set("limit", exp.Limit(expression=exp.Literal.number(limit)))


def _fetch_option(node: exp.Fetch, *names: str) -> bool:
    options = node.args.get("limit_options")
    return options is not None and any(options.args.get(name) for name in names)


def _literal_row_limit(node: exp.Expression | None) -> int | None:
    """The row count of `LIMIT n` / `FETCH FIRST n ROWS`, or None if missing or not a plain
    number (LIMIT ALL, LIMIT NULL, LIMIT (SELECT ...), LIMIT '5' are all replaced).
    """
    if isinstance(node, exp.Limit):
        value = node.expression
    elif isinstance(node, exp.Fetch):
        value = node.args.get("count")
    else:
        return None
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.this)
    return None


def _identifier_name(node: exp.Expression) -> str:
    """A table/schema name as Postgres sees it: unquoted names are folded to lowercase."""
    if isinstance(node, exp.Identifier) and node.quoted:
        return node.name
    return node.name.lower()


def _function_name(func: exp.Func) -> str:
    if isinstance(func, exp.Anonymous):
        return func.name
    return func.sql_name().lower()


def _describe_node(node: exp.Expression) -> str:
    """Short label for an error message, e.g. 'DELETE', 'INTO', 'FOR UPDATE'."""
    labels = {exp.Into: "INTO", exp.Lock: "FOR UPDATE / FOR SHARE"}
    return labels.get(type(node), node.key.upper())
