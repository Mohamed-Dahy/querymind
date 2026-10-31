/**
 * Column types, formatting and sorting for result rows.
 *
 * The API sends rows without column types (api/schemas.py: `rows: list[list[JsonValue]]`), and
 * the executor turns database values into JSON (db/executor.py: Decimal → number, dates → ISO
 * text). So the type is read from the values themselves. Shared by the table (U1.4) and the chart
 * choice (U1.5); U7.7 moves the chart choice to the backend, which knows the real types.
 */

export type ColumnType = 'number' | 'date' | 'datetime' | 'boolean' | 'text' | 'empty'

// What db/executor.py's isoformat() writes for a date, and for a timestamp (with or without zone).
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/
const ISO_DATETIME = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$/

/**
 * The type of one column, from its values. Nulls don't count; a column is a number (or date…)
 * only if every other value is one, so a single stray text value makes it text.
 */
export function inferColumnType(values: readonly unknown[]): ColumnType {
  const present = values.filter((value) => value !== null && value !== undefined)
  if (present.length === 0) return 'empty'
  if (present.every((value) => typeof value === 'number')) return 'number'
  if (present.every((value) => typeof value === 'boolean')) return 'boolean'
  if (present.every((value) => typeof value === 'string' && ISO_DATE.test(value))) return 'date'
  if (
    present.every(
      (value) => typeof value === 'string' && (ISO_DATE.test(value) || ISO_DATETIME.test(value)),
    )
  ) {
    return 'datetime'
  }
  return 'text'
}

/** Every column's type: `columnTypes(['a', 'b'], rows)` → `['text', 'number']`. */
export function columnTypes(
  columns: readonly string[],
  rows: readonly (readonly unknown[])[],
): ColumnType[] {
  return columns.map((_, index) => inferColumnType(rows.map((row) => row[index])))
}

/**
 * A date or timestamp as a Date. "1997-07-04" is read as a *local* date: `new Date("1997-07-04")`
 * would mean midnight UTC, which shows as July 3 west of Greenwich.
 */
export function parseDate(value: string): Date {
  if (ISO_DATE.test(value)) {
    const [year = 0, month = 1, day = 1] = value.split('-').map(Number)
    return new Date(year, month - 1, day)
  }
  // A timestamp without a zone is local time; one with a zone is that exact moment.
  return new Date(value.replace(' ', 'T'))
}

// The browser's language decides the separators: 1,234.5 in English, 1.234,5 in German.
const NUMBER = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })
const DATE = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' })
const DATETIME = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

/** A value as text for people. `null` is the caller's job (the table shows a muted "null"). */
export function formatValue(value: unknown, type: ColumnType): string {
  if (value === null || value === undefined) return ''
  if (type === 'number' && typeof value === 'number') return NUMBER.format(value)
  if ((type === 'date' || type === 'datetime') && typeof value === 'string') {
    const date = parseDate(value)
    if (!Number.isNaN(date.getTime())) {
      return (type === 'date' || ISO_DATE.test(value) ? DATE : DATETIME).format(date)
    }
  }
  return typeof value === 'string' ? value : JSON.stringify(value)
}

// "Item 2" before "Item 10", and "apple" next to "Apple".
const TEXT = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Ascending order of two non-null values of a column of `type` (negative: a comes first). */
export function compareValues(a: unknown, b: unknown, type: ColumnType): number {
  if (type === 'number' && typeof a === 'number' && typeof b === 'number') return a - b
  if (type === 'boolean') return Number(a) - Number(b)
  if ((type === 'date' || type === 'datetime') && typeof a === 'string' && typeof b === 'string') {
    return parseDate(a).getTime() - parseDate(b).getTime()
  }
  return TEXT.compare(asText(a), asText(b))
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value)
}
