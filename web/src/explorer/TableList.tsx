import { Search, ShieldAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import { cn } from '@/lib/utils'

import {
  businessLabel,
  formatRowCount,
  matchesSearch,
  neighboursOf,
  type ExplorerTable,
  type Highlights,
} from './data'

type TableListProps = {
  tables: readonly ExplorerTable[]
  focused: ExplorerTable | undefined
  highlights: Highlights
  onFocus: (table: string | null) => void
}

/** Every table, by business: the way in by keyboard and on a phone, with what the graph shows. */
export function TableList({ tables, focused, highlights, onFocus }: TableListProps) {
  const [search, setSearch] = useState('')
  const searchId = useId()
  const found = tables.filter((table) => matchesSearch(table, search))
  const businesses = [...new Set(found.map(({ business }) => business))].sort((a, b) =>
    businessLabel(a).localeCompare(businessLabel(b)),
  )

  return (
    <section aria-label="List of tables" className="min-w-0 lg:sticky lg:top-4 lg:self-start">
      <label htmlFor={searchId} className="sr-only">
        Search tables, other words for them, and columns
      </label>
      <div className="relative">
        <Search aria-hidden="true" className="absolute top-2 left-2 size-4 text-fg-muted" />
        <input
          id={searchId}
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Table, other word, or column"
          className="h-8 w-full rounded-lg border border-input bg-muted pr-2 pl-7 text-sm outline-none placeholder:text-fg-muted focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:h-11"
        />
      </div>
      <p aria-live="polite" className="mt-2 text-xs text-fg-muted">
        {found.length === tables.length
          ? `${tables.length} tables`
          : `${found.length} of ${tables.length} tables`}
      </p>

      {found.length === 0 ? (
        <p className="mt-2 text-sm text-fg-muted">No table matches.</p>
      ) : (
        <div className="mt-2 space-y-4 lg:max-h-[70dvh] lg:overflow-y-auto lg:pr-1">
          {businesses.map((business) => (
            <section key={business ?? 'other'} aria-label={businessLabel(business)}>
              <h2 className="px-2 text-xs tracking-wide text-fg-muted uppercase">
                {businessLabel(business)}
              </h2>
              <ul className="mt-1 space-y-0.5">
                {found
                  .filter((table) => table.business === business)
                  .map((table) => (
                    <TableRow
                      key={table.name}
                      table={table}
                      focused={table.name === focused?.name}
                      highlights={highlights}
                      onFocus={onFocus}
                    />
                  ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </section>
  )
}

function TableRow({
  table,
  focused,
  highlights,
  onFocus,
}: {
  table: ExplorerTable
  focused: boolean
  highlights: Highlights
  onFocus: (table: string | null) => void
}) {
  const near = neighboursOf(table)
  // Chosen in the graph, or by a link: the list has to show which one it is.
  const row = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (focused) row.current?.scrollIntoView?.({ block: 'nearest' })
  }, [focused])

  return (
    <li ref={row} className={cn('rounded-lg', focused && 'bg-muted')}>
      <button
        type="button"
        aria-pressed={focused}
        onClick={() => onFocus(focused ? null : table.name)}
        className="flex min-h-11 w-full flex-wrap items-center justify-between gap-x-2 rounded-lg px-2 py-1 text-left text-sm hover:bg-muted"
      >
        <span className={cn('min-w-0 break-all', focused && 'font-semibold')}>{table.name}</span>
        <span className="flex items-center gap-1.5 text-xs text-fg-muted">
          {formatRowCount(table.rowCount)}
          {table.piiColumns.length > 0 && (
            <span className="inline-flex items-center gap-0.5 text-warn">
              <ShieldAlert aria-hidden="true" className="size-3" />
              PII
            </span>
          )}
          {highlights.used.has(table.name) && <span className="font-medium text-signal">used</span>}
          {highlights.shown.has(table.name) && <span className="text-info">shown</span>}
        </span>
      </button>
      {focused && (
        <div className="px-2 pb-2 text-xs">
          <Link
            to={`/explore/tables/${encodeURIComponent(table.name)}`}
            className="mb-1 inline-flex min-h-8 items-center rounded-lg text-sm text-info underline underline-offset-4 pointer-coarse:min-h-11"
          >
            Everything about {table.name}
          </Link>
          {near.length === 0 ? (
            <p className="text-fg-muted">This table joins no other table.</p>
          ) : (
            <>
              <p className="text-fg-muted">Joins:</p>
              <ul className="mt-1 flex flex-wrap gap-1">
                {near.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      onClick={() => onFocus(name)}
                      className="min-h-8 rounded-md border border-input px-2 hover:bg-surface pointer-coarse:min-h-11"
                    >
                      {name}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </li>
  )
}
