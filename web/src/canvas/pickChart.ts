/**
 * Which chart fits a result, chosen from its columns and values (no LLM, no server call).
 *
 * The rules (plan_ui.md U1.5):
 * - a time column (a date, or a whole-number column named year/month/…) + a number → **line**,
 *   in time order;
 * - one text column + one number column, at most 30 rows → **horizontal bar**, biggest first;
 * - anything else (one row, no number, too many bars…) → **no chart**: the table says it better.
 * Pure functions, tested on the real recorded answers (pickChart.test.ts). U7.7 moves the choice
 * to the backend, which knows the real column types.
 */
import { columnTypes, compareValues, formatValue, parseDate, type ColumnType } from '@/lib/columns'

/** More bars than this can't be read (or labelled) in a cell. */
export const MAX_BARS = 30
/** Lines past this many become a tangle; the rest stay in the table. */
const MAX_LINES = 3
// A whole-number column with one of these names is a period ("month 1–12", "year 1997").
const PERIOD_NAME =
  /^(year|quarter|month|week|day)$|_(year|quarter|month|week|day)$|^(year|month)_/i

export type ChartPoint = { label: string; values: (number | null)[] }

export type ChartSpec =
  | { kind: 'none'; reason: string }
  | {
      kind: 'bar' | 'line'
      /** The column along the axis (the categories, or the time). */
      xName: string
      /** One series per number column (a bar chart has exactly one). */
      seriesNames: string[]
      points: ChartPoint[]
    }

type Rows = readonly (readonly unknown[])[]

/** The chart for these rows, or why there is none. */
export function pickChart(columns: readonly string[], rows: Rows): ChartSpec {
  if (rows.length < 2) return { kind: 'none', reason: 'One row: the number says it all.' }
  const types = columnTypes(columns, rows)

  const time = types.findIndex((type, index) => isTime(columns[index] ?? '', type, rows, index))
  const numbers = types.flatMap((type, index) =>
    type === 'number' && index !== time ? [index] : [],
  )
  if (numbers.length === 0) return { kind: 'none', reason: 'No numbers to draw.' }

  if (time !== -1) return lineChart(columns, rows, time, types[time] ?? 'text', numbers)

  const texts = types.flatMap((type, index) => (type === 'text' ? [index] : []))
  if (texts.length === 1 && numbers.length === 1) {
    if (rows.length > MAX_BARS) {
      return { kind: 'none', reason: `More than ${MAX_BARS} bars would be too many to read.` }
    }
    return barChart(columns, rows, texts[0] as number, numbers[0] as number)
  }
  return { kind: 'none', reason: 'No chart fits these columns; the table shows them all.' }
}

/** A date column, or whole numbers in a column named like a period ("month", "order_year"). */
function isTime(name: string, type: ColumnType, rows: Rows, index: number): boolean {
  if (type === 'date' || type === 'datetime') return true
  return (
    type === 'number' &&
    PERIOD_NAME.test(name) &&
    rows.every((row) => row[index] === null || Number.isInteger(row[index]))
  )
}

function lineChart(
  columns: readonly string[],
  rows: Rows,
  time: number,
  timeType: ColumnType,
  numbers: number[],
): ChartSpec {
  const series = numbers.slice(0, MAX_LINES)
  const isMonth = /month/i.test(columns[time] ?? '') && timeType === 'number'
  const points = rows
    .filter((row) => row[time] !== null && row[time] !== undefined)
    .toSorted((a, b) => compareValues(a[time], b[time], timeType))
    .map((row) => ({
      label: isMonth ? monthName(row[time] as number) : timeLabel(row[time], timeType),
      values: series.map((index) => asNumber(row[index])),
    }))
  return {
    kind: 'line',
    xName: columns[time] ?? '',
    seriesNames: series.map((index) => columns[index] ?? ''),
    points,
  }
}

function barChart(columns: readonly string[], rows: Rows, text: number, number: number): ChartSpec {
  const points = rows
    .map((row) => ({ label: labelOf(row[text]), values: [asNumber(row[number])] }))
    // Biggest first; rows without a value go last.
    .toSorted((a, b) => (b.values[0] ?? -Infinity) - (a.values[0] ?? -Infinity))
  return {
    kind: 'bar',
    xName: columns[text] ?? '',
    seriesNames: [columns[number] ?? ''],
    points,
  }
}

/** Month 1–12 as a short name in the browser's language ("Jan"); other numbers as they are. */
function monthName(month: number): string {
  if (month < 1 || month > 12) return String(month)
  return new Intl.DateTimeFormat(undefined, { month: 'short' }).format(new Date(2000, month - 1, 1))
}

function timeLabel(value: unknown, type: ColumnType): string {
  if (type === 'number') return String(value)
  // A whole day at midnight reads better without "00:00".
  if (typeof value === 'string' && type === 'datetime' && parseDate(value).getHours() === 0) {
    return formatValue(value.slice(0, 10), 'date')
  }
  return formatValue(value, type)
}

function labelOf(value: unknown): string {
  return value === null || value === undefined ? '(empty)' : formatValue(value, 'text')
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' ? value : null
}

/**
 * The chart in words, for screen readers (the drawing itself is an image to them):
 * "Bar chart of total_sales by category_name, 8 bars. Highest: Beverages, 267,868.18. Lowest: …".
 */
export function describeChart(spec: ChartSpec): string {
  if (spec.kind === 'none') return spec.reason
  const { kind, xName, seriesNames, points } = spec
  const count = `${points.length} ${kind === 'bar' ? 'bars' : 'points'}`
  const parts = [
    kind === 'bar'
      ? `Bar chart of ${seriesNames[0]} by ${xName}, ${count}.`
      : `Line chart of ${seriesNames.join(', ')} by ${xName}, ${count}, from ${points[0]?.label} to ${points.at(-1)?.label}.`,
  ]
  // Highest and lowest of the first series: the facts a sighted reader takes from the shape.
  const valued = points.filter((point) => point.values[0] !== null && point.values[0] !== undefined)
  if (valued.length > 0) {
    const byValue = valued.toSorted((a, b) => (b.values[0] as number) - (a.values[0] as number))
    const highest = byValue[0] as ChartPoint
    const lowest = byValue.at(-1) as ChartPoint
    const name = seriesNames.length > 1 ? `${seriesNames[0]}: ` : ''
    parts.push(
      `${name}Highest: ${highest.label}, ${formatValue(highest.values[0], 'number')}.`,
      `Lowest: ${lowest.label}, ${formatValue(lowest.values[0], 'number')}.`,
    )
  }
  return parts.join(' ')
}
