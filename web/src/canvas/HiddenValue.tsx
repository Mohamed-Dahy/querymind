import { Lock } from 'lucide-react'

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

// The value the API puts in place of personal data for a role that may not see it (PLAN 5.4).
export const MASK = '***'

export const HIDDEN_WHY = 'Personal data, hidden for your role.'

/**
 * A masked value in the result table (U7.3): "hidden" in words, not "***". The tooltip says why on
 * hover; it's not focusable (a table of them would be hundreds of tab stops), so the answer's
 * HiddenColumnsNote says the same for keyboards and screen readers.
 */
export function HiddenValue() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex items-center gap-1 rounded-full border border-dashed px-2 py-0.5 text-xs text-fg-muted">
          <Lock aria-hidden="true" className="size-3 shrink-0" />
          hidden
        </span>
      </TooltipTrigger>
      <TooltipContent>{HIDDEN_WHY}</TooltipContent>
    </Tooltip>
  )
}

/**
 * Under the answer (U7.3): which columns were hidden, so "***" in the CSV and "hidden" in the table
 * aren't a mystery. Personal data is shown to admins only (api/deps.py masks_pii).
 */
export function HiddenColumnsNote({
  columns,
  masked,
}: {
  columns: readonly string[]
  masked: readonly number[] | undefined
}) {
  const names = (masked ?? []).flatMap((index) => columns[index] ?? [])
  if (names.length === 0) return null
  const count = names.length === 1 ? '1 column' : `${names.length} columns`
  return (
    <p className="flex items-start gap-1.5 text-sm text-fg-muted">
      <Lock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        {count} hidden for your role:{' '}
        <span className="font-medium text-fg">{names.join(', ')}</span>. Only admins see personal
        data; the CSV keeps the *** too.
      </span>
    </p>
  )
}
