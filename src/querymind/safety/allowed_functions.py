"""The functions generated SQL may call. Everything not listed here is rejected.

Why an allowlist and not a blocklist: Postgres has thousands of functions, and some are
dangerous (`pg_sleep` hangs a connection, `set_config` can switch off our timeout,
`pg_read_file` / `lo_import` read server files, `dblink` connects to other servers). With a
blocklist, any function we forgot is allowed. With an allowlist, any function we forgot is
blocked, and the worst case is a rejected question, never a successful attack.

sqlglot turns each function it knows into its own class (e.g. `count(...)` → `exp.Count`,
`date_trunc(...)` → `exp.TimestampTrunc`) and any unknown one into `exp.Anonymous` with the
name as text. So the allowlist has two parts: classes, and names for the Anonymous ones.
"""

from sqlglot import exp

ALLOWED_FUNCTION_TYPES: frozenset[type[exp.Func]] = frozenset(
    {
        # Aggregates
        exp.Count,
        exp.Sum,
        exp.Avg,
        exp.Min,
        exp.Max,
        exp.GroupConcat,  # string_agg
        exp.ArrayAgg,
        exp.Stddev,
        exp.StddevPop,
        exp.StddevSamp,
        exp.Variance,
        exp.VariancePop,
        exp.Corr,
        exp.LogicalAnd,  # bool_and
        exp.LogicalOr,  # bool_or
        exp.PercentileCont,
        exp.PercentileDisc,
        exp.Mode,
        # Window functions
        exp.RowNumber,
        exp.Rank,
        exp.DenseRank,
        exp.PercentRank,
        exp.CumeDist,
        exp.Ntile,
        exp.Lag,
        exp.Lead,
        exp.FirstValue,
        exp.LastValue,
        exp.NthValue,
        # Math
        exp.Abs,
        exp.Round,
        exp.Ceil,
        exp.Floor,
        exp.Trunc,
        exp.Sqrt,
        exp.Pow,
        exp.Ln,
        exp.Log,
        exp.Exp,
        exp.Sign,
        exp.Greatest,
        exp.Least,
        # Text
        exp.Upper,
        exp.Lower,
        exp.Initcap,
        exp.Length,
        exp.Substring,
        exp.Trim,
        exp.Concat,
        exp.ConcatWs,
        exp.Replace,
        exp.StrPosition,  # position / strpos
        exp.SplitPart,
        exp.Left,
        exp.Right,
        exp.Pad,  # lpad / rpad
        exp.Reverse,
        exp.RegexpReplace,
        # Dates and times
        exp.CurrentDate,
        exp.CurrentTimestamp,  # also now()
        exp.TimestampTrunc,  # date_trunc
        exp.Extract,  # also date_part
        exp.TimeToStr,  # to_char
        exp.StrToDate,  # to_date
        exp.UnixToTime,  # to_timestamp
        exp.Date,
        # Conditionals and types
        exp.Case,
        exp.If,  # the WHEN branches of a CASE
        exp.Coalesce,
        exp.Nullif,
        exp.Cast,  # CAST(x AS t) and x::t
        exp.Exists,  # WHERE EXISTS (SELECT ...)
        # Rows and arrays
        exp.ExplodingGenerateSeries,  # generate_series
        exp.Explode,  # unnest
        exp.Array,  # ARRAY[...]
        exp.ArraySize,  # array_length
        # Operators, not functions: sqlglot 30 models these as Func subclasses, so they must be
        # listed or every query using them is blocked (AND/OR were, until the golden set in
        # step 1.8 caught it). test_sql_validator.py tries every common operator to guard this.
        exp.And,
        exp.Or,
        exp.RegexpLike,  # a ~ 'pattern', a !~ 'pattern'
        exp.RegexpILike,  # a ~* 'pattern'
        exp.Collate,  # a COLLATE "C"
    }
)

# Functions sqlglot has no class for (they parse as exp.Anonymous), by lowercase name.
ALLOWED_ANONYMOUS_FUNCTIONS: frozenset[str] = frozenset(
    {
        "age",
        "make_date",
        "make_interval",
        "make_timestamp",
        "width_bucket",
    }
)
