import { useQuery } from '@tanstack/react-query'
import { CircleCheck, Loader2, OctagonX } from 'lucide-react'

import { ApiError, getHealth } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'

export default function App() {
  // TanStack Query runs the request, caches the result under this key, and gives one of three states.
  // The `signal` cancels the request when nothing needs it any more (e.g. the page goes away).
  const health = useQuery({ queryKey: ['health'], queryFn: ({ signal }) => getHealth(signal) })

  return (
    <div className="canvas-grid min-h-dvh">
      <main className="mx-auto max-w-2xl space-y-6 px-4 py-12">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h1 className="text-3xl">QueryMind</h1>
          <ThemeSwitcher />
        </div>

        <div className="rounded-cell border bg-surface p-6 shadow-sm">
          {health.isPending && (
            <p role="status" className="flex items-center gap-2 text-fg-muted">
              <Loader2 aria-hidden="true" className="size-4 animate-spin" />
              Checking the API…
            </p>
          )}
          {health.isSuccess && (
            <p role="status" className="flex items-center gap-2">
              <CircleCheck aria-hidden="true" className="size-4 text-signal" />
              API {health.data.status} · version{' '}
              <span className="font-mono tabular-nums">{health.data.version}</span>
            </p>
          )}
          {health.isError && (
            <div role="alert" className="flex items-start gap-2">
              <OctagonX aria-hidden="true" className="mt-1 size-4 shrink-0 text-danger" />
              <div>
                <p className="font-medium">
                  {errorTitle(health.error instanceof ApiError ? health.error.code : '')}
                </p>
                <p className="text-sm text-fg-muted">{health.error.message}</p>
              </div>
            </div>
          )}
        </div>

        {import.meta.env.DEV && (
          <p className="text-sm text-fg-muted">
            Dev:{' '}
            <a className="text-info underline underline-offset-4" href="/?styleguide">
              style guide
            </a>
          </p>
        )}
      </main>
    </div>
  )
}
