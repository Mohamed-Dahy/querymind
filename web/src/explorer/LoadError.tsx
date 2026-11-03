import { ApiError } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { Button } from '@/components/ui/button'

/** The explorer's data couldn't be read: what went wrong, and a way to try again. */
export function LoadError({ error, onRetry }: { error: Error | null; onRetry: () => void }) {
  return (
    <div className="rounded-lg border bg-surface p-4">
      <p className="font-semibold">
        {errorTitle(error instanceof ApiError ? error.code : 'unexpected')}
      </p>
      <p className="mt-1 text-sm text-fg-muted">
        The tables can&apos;t be listed right now. Questions on the canvas may not work either.
      </p>
      <Button type="button" variant="outline" className="mt-3" onClick={onRetry}>
        Try again
      </Button>
    </div>
  )
}
