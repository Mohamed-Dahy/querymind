import { useEffect, useState } from 'react'

/**
 * Whole seconds until `until` (a timestamp), counting down while the component is on screen.
 * 0 when there is nothing to wait for. It counts from when the component appeared, so mount it
 * when the wait starts: the canvas's error cell appears with its error; SubmitButton keys its
 * countdown by the wait.
 */
export function useSecondsLeft(until: number | undefined): number {
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
