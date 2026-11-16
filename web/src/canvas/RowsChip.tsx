import { Filter } from 'lucide-react'

/**
 * "Limited to your rows: Eastern" (U7.2, PLAN 5.3): on an answer whose rows the database's row
 * security filtered for this user. The API sets `row_scope` only when the SQL read a table with
 * policies, so the chip never claims a limit that didn't apply. Words, not colour alone.
 */
export function RowsChip({ tags }: { tags: readonly string[] | null | undefined }) {
  if (!tags || tags.length === 0) return null
  return (
    <p className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs">
      <Filter aria-hidden="true" className="size-3.5 shrink-0" />
      <span>
        Limited to your rows: <span className="font-medium">{tags.join(', ')}</span>
      </span>
    </p>
  )
}
