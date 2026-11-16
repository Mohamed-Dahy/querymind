import { Loader2 } from 'lucide-react'

import { formatWait } from '@/canvas/wait'
import { Button } from '@/components/ui/button'
import { useSecondsLeft } from '@/lib/useSecondsLeft'

type SubmitButtonProps = {
  label: string
  /** Shown while the request runs, e.g. "Testing the key…". */
  busyLabel: string
  busy: boolean
  /** After a `rate_limited` answer: when trying again makes sense (a timestamp). */
  retryAt: number | undefined
}

/**
 * A form's submit button that also waits out a rate limit (U7.1b; the Settings forms share it):
 * a spinner while the request runs, then "Try again in 30 seconds" until the wait is over.
 */
export function SubmitButton(props: SubmitButtonProps) {
  // Keyed by the wait: each new one mounts a fresh countdown, which counts from that moment (a form
  // open for minutes before its first 429 must still say 30 seconds).
  return <Countdown key={props.retryAt ?? 'none'} {...props} />
}

function Countdown({ label, busyLabel, busy, retryAt }: SubmitButtonProps) {
  const secondsLeft = useSecondsLeft(retryAt)
  const waiting = secondsLeft > 0

  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      {/* aria-disabled while waiting, not disabled: a disabled button would drop keyboard focus.
          Cancelling the click also stops Enter in a field from sending the form. */}
      <Button
        type="submit"
        size="lg"
        disabled={busy}
        aria-busy={busy}
        aria-disabled={waiting || undefined}
        onClick={(event) => {
          if (waiting) event.preventDefault()
        }}
        className="pointer-coarse:min-h-11 aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
      >
        {busy && <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />}
        {busy ? busyLabel : label}
      </Button>
      {/* Hidden from screen readers: a number changing every second is noise. They hear the wait
          in the form's error, and its end just below. */}
      {waiting && (
        <span aria-hidden="true" className="text-sm text-fg-muted tabular-nums">
          Try again in {formatWait(secondsLeft)}
        </span>
      )}
      <p aria-live="polite" className="sr-only">
        {retryAt !== undefined && !waiting ? 'You can try again now.' : ''}
      </p>
    </div>
  )
}
