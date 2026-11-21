import { Search, ShieldAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router'

import { SEARCH_FIELD } from '@/components/field'
import { cn } from '@/lib/utils'

import {
  businessLabel,
  formatRowCount,
  matchesSearch,
  neighboursOf,
  type ExplorerTable,
  type Highlights,
} from './data'
import { useExplorerHref } from './useExplorerDatabase'

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
    <section
      aria-label="List of tables"
      className="min-w-0 lg:sticky lg:top-4 lg:self-start lg:rounded-cell lg:border lg:bg-surface lg:p-3"
    >
      <label htmlFor={searchId} className="sr-only">
        Search tables, other words for them, and columns
      </label>
      <div className="relative">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-muted"
        />
        <input
          id={searchId}
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Table, other word, or column"
          className={SEARCH_FIELD}
        />
      </div>
      <p aria-live="polite" className="mt-2 px-1 font-mono text-xs text-fg-muted tabular-nums">
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
              {/* CSS capitals, as on the graph's frames: the heading's text stays "Chinook". */}
              <h2 className="label-mono px-2 uppercase">{businessLabel(business)}</h2>
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
  const href = useExplorerHref()
  // Chosen in the graph, or by a link: the list has to show which one it is.
  const row = useRef<HTMLLIElement>(null)
  useEffect(() => {
    if (focused) row.current?.scrollIntoView?.({ block: 'nearest' })
  }, [focused])

  return (
    <li
      ref={row}
      className={cn(
        'rounded-lg transition-colors duration-150',
        // The chosen table: a lime edge, like the open conversation in the history.
        focused && 'bg-muted shadow-[inset_2px_0_0_var(--signal)]',
      )}
    >
      <button
        type="button"
        aria-pressed={focused}
        onClick={() => onFocus(focused ? null : table.name)}
        className="flex min-h-11 w-full flex-wrap items-center justify-between gap-x-2 rounded-lg px-2.5 py-1 text-left text-sm transition-colors duration-150 hover:bg-muted"
      >
        <span className={cn('min-w-0 break-all', focused && 'font-semibold')}>{table.name}</span>
        <span className="flex items-center gap-1.5 font-mono text-xs text-fg-muted tabular-nums">
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
        <div className="px-2.5 pb-2.5 text-xs">
          <Link
            to={href(`/explore/tables/${encodeURIComponent(table.name)}`)}
            className="mb-1 inline-flex min-h-8 items-center rounded-lg text-sm text-info underline underline-offset-4 pointer-coarse:min-h-11"
          >
            Everything about {table.name}
          </Link>
          {near.length === 0 ? (
            <p className="text-fg-muted">This table joins no other table.</p>
          ) : (
            <>
              <p className="label-mono">Joins:</p>
              <ul className="mt-1 flex flex-wrap gap-1">
                {near.map((name) => (
                  <li key={name}>
                    <button
                      type="button"
                      onClick={() => onFocus(name)}
                      className="min-h-8 rounded-full border border-input bg-surface px-2.5 font-mono transition-colors duration-150 hover:border-signal pointer-coarse:min-h-11"
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
