import { formatWait } from '@/canvas/wait'
import { useSecondsLeft } from '@/lib/useSecondsLeft'

/**
 * After a "too many checks" (429): when trying again makes sense, counting down. Give it
 * `key={retryAt}` where it's used, so it counts from the 429 (useSecondsLeft counts from when it
 * appears).
 */
export function RetryNote({ retryAt, action }: { retryAt: number; action: string }) {
  const secondsLeft = useSecondsLeft(retryAt)
  return (
    <p className="mt-1.5 text-xs text-fg-muted tabular-nums">
      {secondsLeft > 0
        ? `You can ${action} in ${formatWait(secondsLeft)}.`
        : `You can ${action} now.`}
    </p>
  )
}
