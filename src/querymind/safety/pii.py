"""PII masking (5.4): personal data is shown masked to every role but admin.

    guard = PiiGuard(schema, pii={("customers", "contact_name"), ...})
    plan = guard.plan(query)        # a ValidatedQuery; raises UnsafeSqlError if it misuses PII
    result = plan.apply(result)     # those columns' values replaced by "***"

The columns that hold personal data come from the semantic layer (the demo's YAML, hand-reviewed)
or a saved database's data dictionary (drafted at onboarding, corrected by an admin; guessed from
the column names until then: `likely_pii`).

Masking a column in the result is not enough on its own. A masked role could still learn a value
by asking yes/no questions: `WHERE contact_name = 'Maria Anders'` returns a row or doesn't, and
`ORDER BY contact_name` or `upper(contact_name)::int` (whose error message quotes the value) leak
too. So the query is checked first, and a personal column may only be:
- **selected as it is** (`c.contact_name`, `c.contact_name AS name`): that result column is masked;
- **counted** (`count(c.email)`, `count(DISTINCT c.email)`): a count reveals no value;
- **grouped by as it is** (`GROUP BY c.contact_name`): the groups are shown masked.
Anything else (a filter, a sort, a join, a function, a calculation, UNION / INTERSECT / EXCEPT,
which compare values) is refused with an UnsafeSqlError, so the LLM rewrites the query.

How: sqlglot's `qualify` resolves every column to its table (and expands `*`), then the query is
walked scope by scope. A column of a CTE or a subquery that is a personal column passed through
is personal too, so `WITH x AS (SELECT contact_name AS n ...) SELECT n FROM x WHERE n LIKE 'A%'`
is refused like the plain one. A query that can't be resolved this way is refused: fail closed.

A query that reads no table with personal data skips all of this.
"""

import itertools
import logging
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.schema import MappingSchema

from querymind.db.executor import QueryResult
from querymind.db.schema import SchemaInfo
from querymind.safety.errors import UnsafeSqlError
from querymind.safety.sql_validator import ValidatedQuery, tables_read

logger = logging.getLogger(__name__)

MASK = "***"  # what a masked value becomes in a result (NULL stays NULL: no value to hide)
HIDDEN = "<hidden>"  # what a personal sample value becomes in a prompt

type Origin = tuple[str, str] | None  # the (table, column) a value comes from, if personal
type Columns = dict[str, Origin]  # a source's columns; None for a function's (no personal data)
type Scopes = tuple[dict[str, Columns | None], ...]  # the sources in reach, innermost first
type Outputs = list[tuple[str, Origin]]  # a query's result columns, in order


# --- Which columns hold personal data -------------------------------------------------------

# Words that name personal data in a column name, e.g. email, home_phone, ship_postal_code.
_PII_WORDS = frozenset(
    {
        "email",
        "phone",
        "mobile",
        "fax",
        "address",
        "street",
        "zip",
        "zipcode",
        "postcode",
        "ssn",
        "passport",
        "iban",
        "dob",
        "birthdate",
        "birthday",
        "surname",
        "firstname",
        "lastname",
        "fullname",
    }
)
# Two-word names, matched as consecutive words: first_name, but not company_name.
_PII_PAIRS = frozenset(
    {
        ("first", "name"),
        ("last", "name"),
        ("full", "name"),
        ("middle", "name"),
        ("contact", "name"),
        ("postal", "code"),
        ("birth", "date"),
        ("date", "birth"),  # date_of_birth, once "of" is dropped
        ("card", "number"),
        ("credit", "card"),
        ("national", "id"),
        ("tax", "id"),
    }
)


def likely_pii(column: str) -> bool:
    """A guess from the name alone (a saved database not yet onboarded): email, home_phone,
    first_name, ship_postal_code... A guess, so the dictionary review can correct it."""
    words = [w for w in re.split(r"[^a-z0-9]+", column.lower()) if w and w != "of"]
    return any(w in _PII_WORDS for w in words) or any(
        pair in _PII_PAIRS for pair in itertools.pairwise(words)
    )


def hide_samples(schema: SchemaInfo, pii: Collection[tuple[str, str]]) -> SchemaInfo:
    """The schema with the sample values of personal columns replaced by <hidden>, so the LLM
    of a masked role never sees one (it could quote it in an explanation)."""
    personal = set(pii)
    tables = []
    for table in schema.tables:
        hidden = {c.name for c in table.columns if (table.name, c.name) in personal}
        if hidden and table.sample_rows:
            rows = tuple(
                tuple(
                    HIDDEN if column.name in hidden and value is not None else value
                    for column, value in zip(table.columns, row, strict=True)
                )
                for row in table.sample_rows
            )
            table = table.model_copy(update={"sample_rows": rows})
        tables.append(table)
    return SchemaInfo(tables=tuple(tables))


# --- The plan: which result columns to mask ------------------------------------------------


@dataclass(frozen=True)
class MaskPlan:
    """What to mask in the result of one checked query.

    `columns` are the result's column names as the check saw them, so a result whose columns
    don't match (a `*` expanded differently than Postgres did) is masked whole, never wrongly.
    """

    columns: tuple[str, ...] = ()
    masked: frozenset[int] = frozenset()  # positions in `columns`

    def apply(self, result: QueryResult) -> QueryResult:
        """The result with the masked columns' values replaced by MASK."""
        if not self.masked:
            return result
        positions = self.masked
        if not _same_columns(self.columns, result.columns):
            logger.warning(
                "masking every column: the result's columns aren't the checked ones",
                extra={"checked": list(self.columns), "result": result.columns},
            )
            positions = frozenset(range(len(result.columns)))
        rows = [
            [MASK if i in positions and value is not None else value for i, value in enumerate(row)]
            for row in result.rows
        ]
        return result.model_copy(update={"rows": rows, "masked_columns": sorted(positions)})


NO_MASK = MaskPlan()


def _same_columns(checked: Sequence[str], returned: Sequence[str]) -> bool:
    """Same count, and the same names where the check knew them. An expression without an alias
    is `_col_N` to sqlglot but `count` or `?column?` to Postgres, so those aren't compared."""
    return len(checked) == len(returned) and all(
        c == r for c, r in zip(checked, returned, strict=True) if not c.startswith("_col_")
    )


class PiiGuard:
    """Checks queries for one database view, and says what to mask (see the module docstring)."""

    def __init__(self, schema: SchemaInfo, pii: Collection[tuple[str, str]]) -> None:
        columns = {table.name: [c.name for c in table.columns] for table in schema.tables}
        # Only pairs that exist: a flag on a dropped column means nothing.
        self.pii = frozenset((t, c) for t, c in pii if c in columns.get(t, ()))
        self._tables = frozenset(t for t, _ in self.pii)
        self._columns = columns
        # normalize=False: names are given exactly as Postgres stores them.
        self._schema = MappingSchema(
            {name: dict.fromkeys(cols, "text") for name, cols in columns.items()},
            dialect="postgres",
            normalize=False,
        )

    def plan(self, query: ValidatedQuery) -> MaskPlan:
        """The masking plan of a validated query.

        Raises:
            UnsafeSqlError: it uses a personal column other than selected, counted or grouped by,
                or it reads personal data in a way that can't be checked.
        """
        if not query.tables & self._tables:
            return NO_MASK  # no personal data in reach: nothing to check or mask
        outputs = self._outputs(query.sql)
        return MaskPlan(
            columns=tuple(name for name, _ in outputs),
            masked=frozenset(i for i, (_, origin) in enumerate(outputs) if origin is not None),
        )

    def allows(self, sql: str) -> bool:
        """True if trusted SQL (a business term, an example) passes the check: what a masked role's
        prompt may show. Quiet, like `tables_read`: nothing is logged."""
        try:
            if tables_read(sql) & self._tables:
                self._outputs(sql)
        except UnsafeSqlError:
            return False
        return True

    def _outputs(self, sql: str) -> Outputs:
        from sqlglot.optimizer.qualify import qualify  # the optimizer is only needed here

        try:
            tree = sqlglot.parse_one(sql, read="postgres")
            qualified = qualify(
                tree, schema=self._schema, dialect="postgres", validate_qualify_columns=True
            )
        except Exception as exc:  # a third-party optimizer: whatever it can't do, we refuse
            raise _cannot_check(f"{type(exc).__name__}: {exc}") from exc
        if not isinstance(qualified, exp.Query):
            raise _cannot_check("not a query")
        return _Taint(self._columns, self.pii).query(qualified, (), {})


# --- The walk ----------------------------------------------------------------------------------


class _Taint:
    """Which result columns carry personal data, refusing every other use of it."""

    def __init__(
        self, columns: Mapping[str, Sequence[str]], pii: frozenset[tuple[str, str]]
    ) -> None:
        self._columns = columns
        self._pii = pii

    def query(self, node: exp.Expr, scopes: Scopes, ctes: Mapping[str, Columns]) -> Outputs:
        """A query's result columns. `scopes`: the sources a correlated subquery can reach."""
        if isinstance(node, exp.Subquery):  # (SELECT ...) in parentheses
            return self.query(node.this, scopes, ctes)
        with_ = node.args.get("with_")
        if with_ is not None:
            if with_.args.get("recursive"):
                raise _cannot_check("a recursive query")
            ctes = dict(ctes)
            for cte in with_.expressions:  # each CTE sees the ones before it
                outputs = self.query(cte.this, scopes, ctes)
                ctes[_alias(cte)] = _renamed(outputs, cte.args.get("alias"))
        if isinstance(node, exp.SetOperation):
            return self._set_operation(node, scopes, ctes)
        if isinstance(node, exp.Select):
            return self._select(node, scopes, ctes)
        raise _cannot_check(f"a {node.key} query")

    def _select(self, node: exp.Select, scopes: Scopes, ctes: Mapping[str, Columns]) -> Outputs:
        sources: dict[str, Columns | None] = {}
        inner: Scopes = (sources, *scopes)  # filled below; a LATERAL source sees earlier ones
        from_ = node.args.get("from_")
        joins = node.args.get("joins") or []
        for source in [from_.this if from_ else None, *(j.this for j in joins)]:
            if source is not None:
                sources[source.alias_or_name] = self._source(source, inner, ctes)
        for join in joins:
            if join.args.get("using"):
                raise _cannot_check("JOIN ... USING")  # qualify turns it into ON; never left
            for key, value in join.args.items():
                if key != "this":
                    self._scan_all(value, inner, ctes, None)

        outputs: Outputs = [self._projection(p, inner, ctes) for p in node.expressions]
        for key, value in node.args.items():
            if key in ("expressions", "from_", "joins", "with_"):
                continue
            self._scan_all(value, inner, ctes, outputs, group=key == "group")
        return outputs

    def _set_operation(
        self, node: exp.SetOperation, scopes: Scopes, ctes: Mapping[str, Columns]
    ) -> Outputs:
        left = self.query(node.this, scopes, ctes)
        right = self.query(node.expression, scopes, ctes)
        if len(left) != len(right):
            raise _cannot_check("set operation sides of different widths")
        outputs = [(name, lo or ro) for (name, lo), (_, ro) in zip(left, right, strict=True)]
        origin = _first(outputs)
        # UNION (without ALL), INTERSECT and EXCEPT compare values: the row count would tell.
        union_all = isinstance(node, exp.Union) and not node.args.get("distinct")
        if origin is not None and not union_all:
            raise _refused(origin)
        for key, value in node.args.items():
            if key not in ("this", "expression", "with_"):
                self._scan_all(value, scopes, ctes, outputs)
        return outputs

    def _source(
        self, source: exp.Expr, scopes: Scopes, ctes: Mapping[str, Columns]
    ) -> Columns | None:
        """A FROM / JOIN source's columns: a table, a CTE, a subquery, or a function (no PII)."""
        if isinstance(source, exp.Table) and isinstance(source.this, exp.Identifier):
            name = source.this.name
            if source.args.get("db") is None and name in ctes:
                columns = ctes[name]
            elif name in self._columns:
                columns = {
                    c: (name, c) if (name, c) in self._pii else None for c in self._columns[name]
                }
            else:
                raise _cannot_check(f"table {name}")
            return _renamed(list(columns.items()), source.args.get("alias"))
        if isinstance(source, exp.Subquery | exp.Lateral) and isinstance(
            source.this, exp.Query | exp.Subquery
        ):
            return _renamed(self.query(source.this, scopes, ctes), source.args.get("alias"))
        # generate_series(...), unnest(...), VALUES: their arguments must not use personal data;
        # what they return is then not personal.
        self._scan(source, scopes, ctes, None)
        return None

    def _projection(
        self, node: exp.Expr, scopes: Scopes, ctes: Mapping[str, Columns]
    ) -> tuple[str, Origin]:
        name = node.alias_or_name
        value = node.this if isinstance(node, exp.Alias) else node
        if isinstance(value, exp.Column) and not isinstance(value.this, exp.Star):
            return name, self._resolve(value, scopes, None)  # selected as it is: masked if PII
        if isinstance(value, exp.Subquery):  # a scalar subquery: its one value, as it is
            return name, _first(self.query(value.this, scopes, ctes))
        self._scan(value, scopes, ctes, None)
        return name, None

    def _scan_all(
        self,
        value: object,
        scopes: Scopes,
        ctes: Mapping[str, Columns],
        outputs: Outputs | None,
        *,
        group: bool = False,
    ) -> None:
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, exp.Expr):
                self._scan(item, scopes, ctes, outputs, group=group)

    def _scan(
        self,
        node: exp.Expr,
        scopes: Scopes,
        ctes: Mapping[str, Columns],
        outputs: Outputs | None,
        *,
        group: bool = False,
    ) -> None:
        """Refuse any personal value used here, unless counted (or grouped by as it is)."""
        # A nested query is checked on its own (pruned here); what it returns is then judged.
        for item in node.walk(prune=lambda n: isinstance(n, exp.Query)):
            if isinstance(item, exp.Query):
                origin = _first(self.query(item, scopes, ctes))
                if origin is not None:  # x IN (SELECT email ...), EXISTS (...), ...
                    raise _refused(origin)
            elif isinstance(item, exp.Column):
                if isinstance(item.this, exp.Star):
                    raise _cannot_check("*")
                origin = self._resolve(item, scopes, outputs)
                if origin is not None and not _counted(item) and not (group and _grouped(item)):
                    raise _refused(origin)
            elif isinstance(item, exp.TableColumn):  # a whole row: `SELECT c FROM customers c`
                self._whole_row(item, scopes)
            elif isinstance(item, exp.Star) and not isinstance(item.parent, exp.Count):
                raise _cannot_check("*")
            elif outputs is not None and _position(item):  # ORDER BY 2
                number = int(item.name)
                origin = outputs[number - 1][1] if 1 <= number <= len(outputs) else None
                if origin is not None:
                    raise _refused(origin)

    def _resolve(self, column: exp.Column, scopes: Scopes, outputs: Outputs | None) -> Origin:
        """Where a column's values come from. Unqualified: a result column named in ORDER BY or
        DISTINCT ON (qualify qualifies every other column)."""
        table = column.table
        if not table:
            for name, origin in outputs or []:
                if name == column.name:
                    return origin
            raise _cannot_check(f"column {column.name}")
        for sources in scopes:
            if table in sources:
                columns = sources[table]
                if columns is None:
                    return None  # a function's column
                if column.name not in columns:
                    raise _cannot_check(f"column {table}.{column.name}")
                return columns[column.name]
        raise _cannot_check(f"column {table}.{column.name}")

    def _whole_row(self, node: exp.TableColumn, scopes: Scopes) -> None:
        for sources in scopes:
            if node.name in sources:
                columns = sources[node.name]
                origin = _first(list(columns.items())) if columns is not None else None
                if origin is not None:
                    raise _refused(origin)
                return
        raise _cannot_check(f"row {node.name}")


def _counted(column: exp.Column) -> bool:
    """count(x) or count(DISTINCT x): a number, no value."""
    child: exp.Expr = column
    parent = column.parent
    if isinstance(parent, exp.Distinct) and child.arg_key == "expressions":
        child, parent = parent, parent.parent
    return isinstance(parent, exp.Count) and child.arg_key == "this"


def _grouped(column: exp.Column) -> bool:
    """GROUP BY x, the column as it is (not an expression on it)."""
    return isinstance(column.parent, exp.Group) and column.arg_key == "expressions"


def _position(node: exp.Expr) -> bool:
    """A number in ORDER BY that means "the n-th result column"."""
    return (
        isinstance(node, exp.Literal)
        and node.is_int
        and isinstance(node.parent, exp.Ordered)
        and isinstance(node.parent.parent, exp.Order)
    )


def _alias(cte: exp.CTE) -> str:
    return str(cte.args["alias"].this.name)


def _renamed(outputs: Sequence[tuple[str, Origin]], alias: exp.Expr | None) -> Columns:
    """A source's columns, renamed by an alias's column list: `AS t(a, b)`."""
    names = [c.name for c in alias.columns] if isinstance(alias, exp.TableAlias) else []
    renamed = [(names[i] if i < len(names) else name, o) for i, (name, o) in enumerate(outputs)]
    return dict(renamed)


def _first(outputs: Sequence[tuple[str, Origin]]) -> Origin:
    return next((origin for _, origin in outputs if origin is not None), None)


def _refused(origin: tuple[str, str]) -> UnsafeSqlError:
    table, column = origin
    return UnsafeSqlError(
        f"Column {table}.{column} is personal data, masked for this user: select it as it is "
        "(it is shown masked), count it, or group by it. Don't filter, sort, join or compute "
        "with it. If the question can't be answered without that, refuse it: say this column is "
        "personal data, hidden from this user."
    )


def _cannot_check(what: str) -> UnsafeSqlError:
    return UnsafeSqlError(
        f"This query reads tables with personal data in a way that can't be checked ({what}). "
        "Select columns by name, without *, whole rows or recursion."
    )
