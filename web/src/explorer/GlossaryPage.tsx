import { ArrowLeft, MessageSquarePlus, Search } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { Link, useLocation } from 'react-router'

import type { MetricMeaning } from '@/api/explorer'
import { SqlCode } from '@/canvas/SqlView'
import { useDraftNew } from '@/canvas/useConversation'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

import { businessLabel, metricMatches, metricQuestion, termAnchor, termName } from './data'
import { LoadError } from './LoadError'
import { useSemanticOverview } from './useExplorerData'

/**
 * Every business term and exactly what it means in SQL. It reads only the semantic layer, so it works
 * even when the database is down.
 */
export default function GlossaryPage() {
  const { data, error, isPending, refetch } = useSemanticOverview()
  const [search, setSearch] = useState('')
  const searchId = useId()
  const { hash } = useLocation()

  // A link to one term, from a table's page. The browser can't do this itself: the term wasn't on
  // the page yet when the address changed.
  const loaded = data !== undefined
  useEffect(() => {
    if (loaded && hash) document.getElementById(hash.slice(1))?.scrollIntoView?.()
  }, [loaded, hash])

  const businesses = (data?.businesses ?? []).map((business) => ({
    ...business,
    metrics: business.metrics.filter((metric) => metricMatches(metric, search)),
  }))
  const total = (data?.businesses ?? []).reduce((sum, { metrics }) => sum + metrics.length, 0)
  const found = businesses.reduce((sum, { metrics }) => sum + metrics.length, 0)

  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <Link
        to="/explore"
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm text-info underline underline-offset-4"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All tables
      </Link>
      <h1 className="mt-2 text-2xl">Business glossary</h1>
      <p className="mt-2 text-fg-muted">
        Every term QueryMind knows, with the exact SQL it uses. A person wrote and checked these, so
        &ldquo;revenue&rdquo; always means the same thing.
      </p>

      <div className="mt-6">
        {isPending ? (
          <Skeleton aria-hidden="true" className="h-64 w-full motion-reduce:animate-none" />
        ) : data === undefined ? (
          <LoadError
            error={error}
            onRetry={() => void refetch()}
            message="The business terms can't be shown right now."
          />
        ) : (
          <>
            <label htmlFor={searchId} className="sr-only">
              Search terms, other words for them, and their meanings
            </label>
            <div className="relative max-w-md">
              <Search aria-hidden="true" className="absolute top-2 left-2 size-4 text-fg-muted" />
              <input
                id={searchId}
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Term, other word, or meaning"
                className="h-8 w-full rounded-lg border border-input bg-muted pr-2 pl-7 text-sm outline-none placeholder:text-fg-muted focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:h-11"
              />
            </div>
            <p aria-live="polite" className="mt-2 text-xs text-fg-muted">
              {found === total ? `${total} terms` : `${found} of ${total} terms`}
            </p>

            {found === 0 ? (
              <p className="mt-4 text-sm text-fg-muted">No term matches.</p>
            ) : (
              <div className="mt-4 space-y-8">
                {businesses
                  .filter(({ metrics }) => metrics.length > 0)
                  .map(({ database, metrics }) => (
                    <section key={database} aria-label={businessLabel(database)}>
                      <h2 className="text-lg font-semibold">{businessLabel(database)}</h2>
                      <ul className="mt-3 space-y-4">
                        {metrics.map((metric) => (
                          <Term key={metric.name} metric={metric} />
                        ))}
                      </ul>
                    </section>
                  ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Term({ metric }: { metric: MetricMeaning }) {
  const draftNew = useDraftNew()
  const { name, kind, description, synonyms, table, sql } = metric
  const title = termName(name)
  const headingId = `${termAnchor(name)}-heading`
  return (
    <li
      id={termAnchor(name)}
      aria-label={title}
      className="scroll-mt-4 rounded-cell border bg-surface p-4"
    >
      <h3 id={headingId} className="font-semibold">
        {title}{' '}
        <span className="text-xs font-normal text-fg-muted">
          {kind === 'measure' ? 'a measure' : 'a condition'}
        </span>
      </h3>
      <p className="mt-1">{description}</p>
      <div className="mt-2">
        <SqlCode sql={sql} />
      </div>
      <p className="mt-2 text-sm">
        <span className="text-fg-muted">On the table:</span>{' '}
        <Link
          to={`/explore/tables/${encodeURIComponent(table)}`}
          className="text-info underline underline-offset-4"
        >
          {table}
        </Link>
      </p>
      {synonyms.length > 0 && (
        <p className="mt-1 text-sm">
          <span className="text-fg-muted">Other words for it:</span> {synonyms.join(', ')}
        </p>
      )}
      <Button
        type="button"
        variant="outline"
        className="mt-3"
        // Every term has this button, so the description says which term it is for.
        aria-describedby={headingId}
        onClick={() => draftNew(metricQuestion(metric))}
      >
        <MessageSquarePlus aria-hidden="true" />
        Ask with this term
      </Button>
    </li>
  )
}
