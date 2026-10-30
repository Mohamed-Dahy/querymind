import { useQuery } from '@tanstack/react-query'
import { CircleCheck, Loader2, OctagonX } from 'lucide-react'

import { ApiError, getHealth } from '@/api/client'
import { errorTitle } from '@/api/errors'

/** Is the API up? A small status line in the top bar (GET /health, cached by TanStack Query). */
export function ApiStatus() {
  // TanStack Query runs the request, caches the result under this key, and gives one of three states.
  // The `signal` cancels the request when nothing needs it any more (e.g. the page goes away).
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => getHealth(signal) })

  if (health.isPending) {
    return (
      <p role="status" className="flex items-center gap-1.5 text-sm text-fg-muted">
        <Loader2 aria-hidden="true" className="size-4 animate-spin" />
        Checking the API…
      </p>
    )
  }

  if (health.isError) {
    return (
      <div role="alert" className="flex max-w-md items-start gap-1.5 text-sm">
        <OctagonX aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
        <p>
          <span className="font-medium">
            {errorTitle(health.error instanceof ApiError ? health.error.code : '')}
          </span>{' '}
          <span className="text-fg-muted">{health.error.message}</span>
        </p>
      </div>
    )
  }

  return (
    <p role="status" className="flex items-center gap-1.5 text-sm text-fg-muted">
      <CircleCheck aria-hidden="true" className="size-4 text-signal" />
      API {health.data.status} · version{' '}
      <span className="font-mono tabular-nums">{health.data.version}</span>
    </p>
  )
}
