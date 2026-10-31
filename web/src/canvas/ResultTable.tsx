import {
  createSortedRowModel,
  rowSortingFeature,
  tableFeatures,
  useTable,
  type ColumnDef,
  type SortDirection,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ArrowUpDown } from 'lucide-react'
import { useMemo, useRef } from 'react'

import { columnTypes, compareValues, formatValue, type ColumnType } from '@/lib/columns'
import { cn } from '@/lib/utils'

type Row = readonly unknown[]

// The table's features, declared once (not per render): sorting, and the sorted row model.
const features = tableFeatures({
  rowSortingFeature,
  sortedRowModel: createSortedRowModel(),
})

// Every row is one line (no wrapping), so the virtualizer can work out positions without measuring.
const ROW_HEIGHT = 36
// Rows drawn above and below the visible ones, so a fast scroll doesn't show blank space.
const OVERSCAN = 10
// The scroll area's height before the browser has measured it (and in tests, where nothing is laid out).
const INITIAL_RECT = { width: 800, height: 384 }

const SORT_WORDS: Record<SortDirection, 'ascending' | 'descending'> = {
  asc: 'ascending',
  desc: 'descending',
}

type ResultTableProps = {
  columns: readonly string[]
  rows: readonly Row[]
  /** What the rows answer, for the table's caption (read by screen readers). */
  question: string
}

/**
 * The result rows: sortable columns, numbers on the right, null shown as "null", and only the
 * rows in view are in the page (virtualized), so 1,000 rows scroll smoothly. Still a real
 * <table>: aria-rowcount and aria-rowindex tell screen readers where they are in all the rows.
 */
export function ResultTable({ columns, rows, question }: ResultTableProps) {
  const types = useMemo(() => columnTypes(columns, rows), [columns, rows])

  const columnDefs = useMemo(
    () =>
      columns.map((name, index): ColumnDef<typeof features, Row> => ({
        // By position: two columns can share a name (SELECT a.name, b.name).
        id: String(index),
        header: name,
        // null → undefined, so sortUndefined: 'last' keeps nulls at the end in both directions.
        accessorFn: (row) => row[index] ?? undefined,
        sortUndefined: 'last',
        sortFn: (rowA, rowB, columnId) =>
          compareValues(rowA.getValue(columnId), rowB.getValue(columnId), types[index] ?? 'text'),
      })),
    [columns, types],
  )

  const table = useTable({
    features,
    columns: columnDefs,
    data: rows as Row[],
    // A click goes ascending → descending → back to the original order, for every type.
    sortDescFirst: false,
  })
  const sortedRows = table.getRowModel().rows

  const scrollArea = useRef<HTMLDivElement>(null)
  // The rule warns that React Compiler can't memoize around this hook; we don't use the compiler.
  // oxlint-disable-next-line react/incompatible-library
  const virtualizer = useVirtualizer({
    count: sortedRows.length,
    getScrollElement: () => scrollArea.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
    initialRect: INITIAL_RECT,
  })
  const items = virtualizer.getVirtualItems()
  // Empty rows above and below the drawn ones keep the scrollbar the size of all the rows.
  const paddingTop = items[0]?.start ?? 0
  const paddingBottom = virtualizer.getTotalSize() - (items.at(-1)?.end ?? 0)

  const sorted = table.state.sorting[0]
  const sortedName = sorted ? columns[Number(sorted.id)] : undefined

  return (
    <div
      ref={scrollArea}
      // A scroll area must be reachable by keyboard (tabIndex 0) to scroll it with the arrow keys.
      role="region"
      aria-label="Result rows"
      tabIndex={0}
      className="max-h-96 overflow-auto rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <table aria-rowcount={rows.length + 1} className="w-full text-sm">
        <caption className="sr-only">
          Result rows for: {question}.{' '}
          {sorted && sortedName
            ? `Sorted by ${sortedName}, ${SORT_WORDS[sorted.desc ? 'desc' : 'asc']}.`
            : 'In the order the query returned them.'}
        </caption>
        <thead className="sticky top-0 z-10 bg-muted">
          {table.getHeaderGroups().map((group) => (
            <tr key={group.id} aria-rowindex={1}>
              {group.headers.map((header) => {
                const direction = header.column.getIsSorted()
                const type = types[Number(header.column.id)] ?? 'text'
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={direction ? SORT_WORDS[direction] : 'none'}
                    className={cn('p-0 font-medium', alignment(type))}
                  >
                    <button
                      type="button"
                      onClick={header.column.getToggleSortingHandler()}
                      className={cn(
                        'inline-flex min-h-9 w-full items-center gap-1.5 px-3 py-2 whitespace-nowrap hover:bg-surface',
                        'outline-none focus-visible:ring-3 focus-visible:ring-ring focus-visible:ring-inset',
                        type === 'number' && 'flex-row-reverse',
                      )}
                    >
                      {String(header.column.columnDef.header)}
                      <SortIcon direction={direction} />
                    </button>
                  </th>
                )
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {paddingTop > 0 && <Spacer height={paddingTop} columns={columns.length} />}
          {items.map((item) => {
            const row = sortedRows[item.index]
            if (!row) return null
            return (
              <tr key={row.id} aria-rowindex={item.index + 2} className="border-t">
                {row.getAllCells().map((cell) => {
                  const value: unknown = cell.getValue()
                  const type = types[Number(cell.column.id)] ?? 'text'
                  return (
                    <td
                      key={cell.id}
                      style={{ height: ROW_HEIGHT }}
                      className={cn('px-3 whitespace-nowrap', alignment(type))}
                    >
                      {value === undefined ? (
                        <span className="text-fg-muted italic">null</span>
                      ) : (
                        formatValue(value, type)
                      )}
                    </td>
                  )
                })}
              </tr>
            )
          })}
          {paddingBottom > 0 && <Spacer height={paddingBottom} columns={columns.length} />}
        </tbody>
      </table>
    </div>
  )
}

/** Numbers line up on the right, with digits of equal width (tabular figures). */
function alignment(type: ColumnType): string {
  return type === 'number' ? 'text-right tabular-nums' : 'text-left'
}

function SortIcon({ direction }: { direction: false | SortDirection }) {
  const Icon = direction === 'asc' ? ArrowUp : direction === 'desc' ? ArrowDown : ArrowUpDown
  return (
    <Icon
      aria-hidden="true"
      className={cn('size-3.5 shrink-0', direction ? 'text-fg' : 'text-fg-muted')}
    />
  )
}

/** Stands in for the rows that aren't drawn; hidden from screen readers (aria-rowindex covers it). */
function Spacer({ height, columns }: { height: number; columns: number }) {
  return (
    <tr aria-hidden="true">
      <td colSpan={columns} style={{ height, padding: 0 }} />
    </tr>
  )
}
