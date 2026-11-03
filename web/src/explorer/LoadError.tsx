import { ApiError } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { Button } from '@/components/ui/button'

type LoadErrorProps = {
  error: Error | null
  onRetry: () => void
  message?: string
}

/** The explorer's data couldn't be read: what went wrong, and a way to try again. */
export function LoadError({
  error,
  onRetry,
  message = "The tables can't be listed right now. Questions on the canvas may not work either.",
}: LoadErrorProps) {
  return (
    <div className="rounded-lg border bg-surface p-4">
      <p className="font-semibold">
        {errorTitle(error instanceof ApiError ? error.code : 'unexpected')}
      </p>
      <p className="mt-1 text-sm text-fg-muted">{message}</p>
      <Button type="button" variant="outline" className="mt-3" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}
