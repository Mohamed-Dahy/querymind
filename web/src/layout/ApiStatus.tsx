import { useQuery } from '@tanstack/react-query'
import { Loader2, OctagonX } from 'lucide-react'

import { ApiError, getHealth } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { cn } from '@/lib/utils'

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
    const title = errorTitle(health.error instanceof ApiError ? health.error.code : '')
    // The same one-line pill as "Checking the API…", so the top bar keeps its height when the API is
    // down (U8.8): the old two-line notice pushed the whole page down (Lighthouse CLS 0.31). The
    // title shows; the details are read out with it and shown on hover, but take no room.
    return (
      <div
        role="alert"
        title={`${title} ${health.error.message}`}
        className={cn(PILL, 'max-w-full min-w-0 border-danger/60 text-fg sm:max-w-72')}
      >
        <OctagonX aria-hidden="true" className="size-3.5 shrink-0 text-danger" />
        <span className="truncate">{title}</span>
        <span className="sr-only"> {health.error.message}</span>
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
