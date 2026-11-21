import { useQuery } from '@tanstack/react-query'
import { Loader2, OctagonX } from 'lucide-react'

import { ApiError, getHealth } from '@/api/client'
import { errorTitle } from '@/api/errors'

/** The pill the status lines share: mono, small, a hairline (Signal Lab, U8.2). */
const PILL =
  'flex items-center gap-2 rounded-full border border-border px-2.5 py-1 font-mono text-xs text-fg-muted'

/**
 * Is the API up? A small status pill in the top bar (GET /health, cached by TanStack Query): a lit
 * lime dot like an instrument's power light, and the words, so colour is never the only signal.
 */
export function ApiStatus() {
  // TanStack Query runs the request, caches the result under this key, and gives one of three states.
  // The `signal` cancels the request when nothing needs it any more (e.g. the page goes away).
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => getHealth(signal) })

  if (health.isPending) {
    return (
      <p role="status" className={PILL}>
        <Loader2 aria-hidden="true" className="size-3.5 animate-spin motion-reduce:animate-none" />
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
    <p role="status" className={PILL}>
      <span aria-hidden="true" className="glow size-1.5 shrink-0 rounded-full bg-signal" />
      API {health.data.status}
      {/* The version is for developers: not worth a phone's row. Screen readers still hear it. */}
      <span className="sr-only sm:not-sr-only">
        {' '}
        · version <span className="font-mono tabular-nums">{health.data.version}</span>
      </span>
    </p>
  )
}
