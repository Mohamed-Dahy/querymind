import { ROWS_TEXT, TIMEOUT_TEXT, TRIES_TEXT } from '@/lib/limits'
import { cn } from '@/lib/utils'

/**
 * The home console's status strip (U9.3, ADR 027): four readings above the question box, like the
 * display row on an instrument. Plain text in a description list, not a live region: it says how the
 * console is set, it doesn't announce news (the API pill in the top bar does that).
 */
export function ConsoleStatus({ source }: { source: string }) {
  const readings = [
    { term: 'SOURCE', value: source },
    { term: 'ACCESS', value: 'Read-only' },
    // The server's defaults (config.py): an operator can change them, so they say so.
    { term: 'LIMITS', value: `${TIMEOUT_TEXT} · ${ROWS_TEXT}`, note: 'by default' },
    { term: 'REPAIRS', value: `Up to ${TRIES_TEXT}`, note: 'by default' },
  ]

  return (
    // 2 columns on a phone, 4 from 640 px. The hairlines are the cells' own borders, so the grid
    // needs no background colour behind it (text always sits on the panel's solid surface).
    <dl className="grid grid-cols-2 border-b sm:grid-cols-4">
      {readings.map(({ term, value, note }, index) => (
        <div
          key={term}
          className={cn(
            'min-w-0 px-4 py-3',
            index % 2 === 1 && 'border-l',
            index < 2 && 'border-b sm:border-b-0',
            index === 2 && 'sm:border-l',
          )}
        >
          <dt className="label-mono">{term}</dt>
          <dd className="mt-1 font-mono text-sm">
            {/* Wraps, never cut off: on a phone a cell is only ~110 px wide, and these words are the
                point of the strip (a phone can't hover for a tooltip). A long database name breaks
                anywhere rather than pushing the page sideways. */}
            <span className="block wrap-anywhere">{value}</span>
            {/* Its own small line, so it is never the part that gets cut off. The space keeps
                "rows by default" apart when a screen reader reads the two lines as one. */}
            {note && <span className="mt-0.5 block text-xs text-fg-muted"> {note}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}
