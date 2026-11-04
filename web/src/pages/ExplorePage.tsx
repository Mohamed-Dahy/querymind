import { lazy, Suspense, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router'

import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { highlightsFrom } from '@/explorer/data'
import { LoadError } from '@/explorer/LoadError'
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

  const highlights = useMemo(
    () => highlightsFrom(params, data?.tables ?? []),
    [params, data?.tables],
  )
  const lit = highlights.used.size + highlights.shown.size > 0

  function onFocus(table: string | null) {
    // Choosing a table keeps the answer's tables, so letting go of it brings them back.
    const next = new URLSearchParams(params)
    if (table === null) next.delete('table')
    else next.set('table', table)
    setParams(next)
  }

  return (
    <div className="mx-auto max-w-7xl px-4 py-10">
      <h1 className="text-2xl">Data explorer</h1>
      <p className="mt-2 text-fg-muted">
        Every table QueryMind can read and how they join, without any of their data. Row counts are
        approximate.
      </p>
      <p className="mt-1">
        <Link
          to="/explore/glossary"
          className="inline-flex min-h-8 items-center rounded-lg text-sm text-info underline underline-offset-4 pointer-coarse:min-h-11"
        >
          Business glossary: what each term means
        </Link>
      </p>

      <div className="mt-6">
        {pending ? (
          <div aria-hidden="true" className="space-y-3">
            <Skeleton className="h-96 w-full motion-reduce:animate-none" />
          </div>
        ) : data === undefined ? (
          <LoadError error={error} onRetry={retry} />
        ) : (
          <>
            {lit && (
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-surface px-3 py-2 text-sm">
                <p>
                  One answer&apos;s tables: <span className="font-medium text-signal">used</span>{' '}
                  means its SQL read them, <span className="text-info">shown</span> means the AI saw
                  them and didn&apos;t need them.
                </p>
                <Button type="button" variant="ghost" size="sm" onClick={() => setParams({})}>
                  Clear
                </Button>
              </div>
            )}
            <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_20rem]">
              {wide && (
                <Suspense
                  fallback={<Skeleton className="h-96 w-full motion-reduce:animate-none" />}
                >
                  <Constellation
                    data={data}
                    focused={focused}
                    highlights={highlights}
                    onFocus={onFocus}
                  />
                </Suspense>
              )}
              <TableList
                tables={data.tables}
                focused={focused}
                highlights={highlights}
                onFocus={onFocus}
              />
            </div>
          </>
        )}
      </div>
    </div>
  )
}
