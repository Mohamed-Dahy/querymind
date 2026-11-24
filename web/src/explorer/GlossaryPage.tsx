import { ArrowLeft, MessageSquarePlus, Search } from 'lucide-react'
import { useEffect, useId, useState } from 'react'
import { Link, useLocation } from 'react-router'

import type { MetricMeaning } from '@/api/explorer'
import { SqlCode } from '@/canvas/SqlView'
import { useDraftNew } from '@/canvas/useConversation'
import { SEARCH_FIELD } from '@/components/field'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

import { businessLabel, metricMatches, metricQuestion, termAnchor, termName } from './data'
import { LoadError } from './LoadError'
import { useSemanticOverview } from './useExplorerData'
import { useExplorerDatabase, useExplorerHref } from './useExplorerDatabase'

/**
 * Every business term and exactly what it means in SQL. It reads only the semantic layer, so it works
 * even when the database is down.
 */
export default function GlossaryPage() {
  const database = useExplorerDatabase()
  const href = useExplorerHref()
  const { data, error, isPending, refetch } = useSemanticOverview(database)
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
        to={href('/explore')}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm text-info underline underline-offset-4"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All tables
      </Link>
      {/* Signal Lab (U8.5): a mono label over the title. */}
      <p aria-hidden="true" className="label-mono mt-4">
        GLOSSARY
      </p>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight">Business glossary</h1>
      <p className="mt-2 max-w-2xl text-fg-muted">
        Every term QueryMind knows, with the exact SQL it uses. A person wrote and checked these, so
        &ldquo;revenue&rdquo; always means the same thing.
      </p>
      {database !== null && (
        <p
          role="note"
          className="mt-4 rounded-cell border bg-surface px-4 py-2.5 text-sm shadow-[inset_2px_0_0_var(--info)]"
        >
          Business terms are written by hand, and so far only for the demo databases. Your own
          database has none yet: questions about it use the meanings of its tables and columns.
        </p>
      )}

      <div className="mt-6">
        {isPending ? (
          <Skeleton
            aria-hidden="true"
            className="h-64 w-full rounded-cell motion-reduce:animate-none"
          />
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
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-fg-muted"
              />
              <input
                id={searchId}
                type="search"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Term, other word, or meaning"
                className={SEARCH_FIELD}
              />
            </div>
            <p aria-live="polite" className="mt-2 font-mono text-xs text-fg-muted tabular-nums">
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
                      <h2 className="flex items-center gap-2 text-lg font-semibold tracking-tight">
                        <span aria-hidden="true" className="size-1.5 rounded-full bg-signal" />
                        {businessLabel(database)}
                      </h2>
                      <ul className="mt-3 grid gap-3 lg:grid-cols-2">
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
  const href = useExplorerHref()
  const draftNew = useDraftNew()
  const { name, kind, description, synonyms, table, sql } = metric
  const title = termName(name)
  const headingId = `${termAnchor(name)}-heading`
  return (
    <li
      id={termAnchor(name)}
      aria-label={title}
      // A link to this term (from a table's page) lights it up: `:target` is the term in the address.
      // min-w-0: a grid item is otherwise never narrower than its longest word (a SQL name),
      // which pushed a 320 px page 7 px sideways; the SQL block scrolls inside itself instead.
      className="flex min-w-0 scroll-mt-4 flex-col rounded-cell border bg-surface p-5 transition-[border-color,box-shadow] duration-200 target:border-signal target:glow"
    >
      <h3 id={headingId} className="font-semibold tracking-tight">
        {title}{' '}
        <span className="ml-1 rounded-full border px-2 py-0.5 align-middle font-mono text-[11px] font-normal text-fg-muted">
          {kind === 'measure' ? 'a measure' : 'a condition'}
        </span>
      </h3>
      <p className="mt-1.5 text-sm">{description}</p>
      <div className="mt-2">
        <SqlCode sql={sql} />
      </div>
      <p className="mt-2 text-sm">
        <span className="text-fg-muted">On the table:</span>{' '}
        <Link
          to={href(`/explore/tables/${encodeURIComponent(table)}`)}
          className="font-mono text-info underline underline-offset-4"
        >
          {table}
        </Link>
      </p>
      {synonyms.length > 0 && (
        <p className="mt-1 text-sm">
          <span className="text-fg-muted">Other words for it:</span> {synonyms.join(', ')}
        </p>
      )}
      {/* Grows, so the buttons line up at the bottom across a row of two terms. */}
      <div aria-hidden="true" className="min-h-3 flex-1" />
      <Button
        type="button"
        variant="outline"
        className="self-start"
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
