import { lazy, Suspense } from 'react'
import { useSearchParams } from 'react-router'

import { ApiError } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TableList } from '@/explorer/TableList'
import { useExplorerData } from '@/explorer/useExplorerData'
import { useMediaQuery } from '@/lib/useMediaQuery'

// Only wider screens draw the graph, so only they download it.
const Constellation = lazy(() =>
  import('@/explorer/Constellation').then(({ Constellation }) => ({ default: Constellation })),
)

const GRAPH_FITS = '(min-width: 768px)'

/** The data explorer: every table and how they join, as a graph and as a list. */
export default function ExplorePage() {
  const { data, error, pending, retry } = useExplorerData()
  const wide = useMediaQuery(GRAPH_FITS)
  // In the URL, so the Back button works and a link can show a chosen table.
  const [params, setParams] = useSearchParams()
  const focused = data?.tables.find(({ name }) => name === params.get('table'))

  function onFocus(table: string | null) {
    setParams(table === null ? {} : { table })
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <h1 className="text-2xl">Data explorer</h1>
      <p className="mt-2 text-fg-muted">
        Every table QueryMind can read and how they join, without any of their data. Row counts are
        approximate.
      </p>

      <div className="mt-6">
        {pending ? (
          <div aria-hidden="true" className="space-y-3">
            <Skeleton className="h-96 w-full motion-reduce:animate-none" />
          </div>
        ) : data === undefined ? (
          <div className="rounded-lg border bg-surface p-4">
            <p className="font-semibold">
              {errorTitle(error instanceof ApiError ? error.code : 'unexpected')}
            </p>
            <p className="mt-1 text-sm text-fg-muted">
              The tables can&apos;t be listed right now. Questions on the canvas may not work
              either.
            </p>
            <Button type="button" variant="outline" className="mt-3" onClick={retry}>
              Try again
            </Button>
          </div>
        ) : (
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
            {wide && (
              <Suspense fallback={<Skeleton className="h-96 w-full motion-reduce:animate-none" />}>
                <Constellation data={data} focused={focused} onFocus={onFocus} />
              </Suspense>
            )}
            <TableList tables={data.tables} focused={focused} onFocus={onFocus} />
          </div>
        )}
      </div>
    </div>
  )
}
