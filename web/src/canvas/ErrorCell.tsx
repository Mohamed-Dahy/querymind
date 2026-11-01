import { CircleAlert, Pencil, RotateCw } from 'lucide-react'
import { useEffect, useState } from 'react'

import { errorTitle } from '@/api/errors'
import { Button } from '@/components/ui/button'

import type { CellError } from './state'
import { formatWait } from './wait'

// The same question would fail the same way, and each try costs LLM quota: change it instead.
const RETRY_WONT_HELP: readonly string[] = ['unsafe_sql', 'invalid_request']

/** Whole seconds until `until` (a timestamp), counting down while the component is on screen. */
function useSecondsLeft(until: number | undefined): number {
  const [now, setNow] = useState(() => Date.now())
  const left = until === undefined ? 0 : Math.max(0, Math.ceil((until - now) / 1000))
  const counting = left > 0
  useEffect(() => {
    if (!counting) return undefined
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [counting])
  return left
}

type ErrorCellProps = {
  error: CellError
  /** A question is running: Retry waits (one question at a time). */
  busy: boolean
  /** Runs the same question again, in this cell. */
  onRetry: () => void
  /** Puts the question back in the composer, to change it. */
  onRephrase: () => void
}

/** Why a question failed, and the way forward: Retry (after any wait), or Rephrase. */
export function ErrorCell({ error, busy, onRetry, onRephrase }: ErrorCellProps) {
  const secondsLeft = useSecondsLeft(error.retryAt)
  const waiting = secondsLeft > 0

  return (
    <div className="flex items-start gap-2 text-sm">
      <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{errorTitle(error.code)}</p>
        {error.message && <p className="text-fg-muted">{error.message}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          {RETRY_WONT_HELP.includes(error.code) ? (
            <Button type="button" variant="outline" onClick={onRephrase}>
              <Pencil aria-hidden="true" />
              Rephrase
            </Button>
          ) : (
            // aria-disabled, not disabled: a disabled button would drop keyboard focus.
            <Button
              type="button"
              variant="outline"
              aria-disabled={busy || waiting}
              className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              onClick={() => {
                if (!busy && !waiting) onRetry()
              }}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          )}
          {/* Hidden from screen readers: a number changing every second would be noise. They hear
              the wait with the error (CanvasPage), and the end of it just below. */}
          {waiting && (
            <span aria-hidden="true" className="text-fg-muted tabular-nums">
              Try again in {formatWait(secondsLeft)}
            </span>
          )}
        </div>
        <p aria-live="polite" className="sr-only">
          {error.retryAt !== undefined && !waiting ? 'You can try again now.' : ''}
        </p>
      </div>
    </div>
  )
}
