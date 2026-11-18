import { ChevronDown, CircleCheck, CircleMinus, CircleX, Loader2 } from 'lucide-react'
import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'

import { useAuditLog, type AuditAction, type AuditFilters, type AuditLine } from '@/api/audit'
import { useMembers } from '@/api/members'
import { useSession } from '@/api/session'
import { useConnections } from '@/api/settings'
import { SqlCode } from '@/canvas/SqlView'
import { FIELD } from '@/components/field'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { DEMO_NAME } from '@/layout/useDatabase'
import { cn } from '@/lib/utils'

import { ACTION_LABELS, ACTIONS, OUTCOME_GROUPS, outcomeOf, type OutcomeKind } from './auditWords'

// Typing in the search waits this long before asking the API (like the history rail's search).
const SEARCH_DELAY_MS = 300
const FILTER_KEYS = ['action', 'outcome', 'email', 'q'] as const

// Short in the row ("Nov 17, 4:02 PM"); the details give the full date.
const WHEN = new Intl.DateTimeFormat('en', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})
const NUMBER = new Intl.NumberFormat('en')
const DOLLARS = new Intl.NumberFormat('en', {
  style: 'currency',
  currency: 'USD',
  maximumSignificantDigits: 2,
})

/**
 * Settings → Audit log (U7.4, PLAN 5.5): who did what in this workspace, newest first. Each line
 * is a row that opens to its details (the SQL, tables, rows, time taken, model and cost, the error's
 * message). The filters (action, outcome, person, words in the question or SQL) apply at once and
 * live in the URL, so a filtered view can be bookmarked or sent to another admin. "Load more"
 * reads the next page. Admins only, like the API.
 */
export default function AuditSettings() {
  const admin = useSession()?.user.role === 'admin'
  const headingId = useId()

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-xl">
        Audit log
      </h2>
      {!admin ? (
        <p className="mt-3 rounded-cell border bg-surface p-4 text-sm">
          Only an admin of this workspace can read its audit log.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-fg-muted">
            Everything done in this workspace, newest first: every question with its SQL and how it
            ended, logins, keys, databases, members and permissions.
          </p>
          <Log />
        </>
      )}
    </section>
  )
}

function useFilters(): [AuditFilters, (key: (typeof FILTER_KEYS)[number], value: string) => void] {
  const [params, setParams] = useSearchParams()
  const filters: AuditFilters = {
    action: (params.get('action') as AuditAction | null) ?? undefined,
    outcome: params.get('outcome') ?? undefined,
    email: params.get('email') ?? undefined,
    q: params.get('q') ?? undefined,
  }
  function setFilter(key: (typeof FILTER_KEYS)[number], value: string) {
    // Replace, not push: Back leaves the page instead of undoing filters one by one.
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value) next.set(key, value)
        else next.delete(key)
        return next
      },
      { replace: true },
    )
  }
  return [filters, setFilter]
}

function Log() {
  const [filters, setFilter] = useFilters()
  const log = useAuditLog(filters)
  const filtered = FILTER_KEYS.some((key) => filters[key])
  const lines = log.data?.pages.flatMap((page) => page.lines) ?? []
  // The search box shows what is typed at once; the URL (and the API) follow after a pause.
  const [search, setSearch] = useState(filters.q ?? '')
  // The latest URL and setter, for the pause's timer: a re-render (a page arriving) mustn't restart it.
  const latest = useRef({ q: filters.q ?? '', setFilter })
  useEffect(() => {
    latest.current = { q: filters.q ?? '', setFilter }
  })
  useEffect(() => {
    const trimmed = search.trim()
    if (trimmed === latest.current.q) return undefined
    const timer = setTimeout(() => latest.current.setFilter('q', trimmed), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [search])

  return (
    <>
      <Filters filters={filters} setFilter={setFilter} search={search} setSearch={setSearch} />
      <div className="mt-5" aria-busy={log.isFetching}>
        {log.isPending ? (
          <Skeleton aria-hidden="true" className="h-72 w-full motion-reduce:animate-none" />
        ) : log.isError ? (
          <LoadError
            error={log.error}
            onRetry={() => void log.refetch()}
            message="The audit log can't be read right now."
          />
        ) : lines.length === 0 ? (
          <div className="rounded-cell border bg-surface p-4 text-sm">
            {filtered ? (
              <>
                <p>No line matches these filters.</p>
                <Button
                  type="button"
                  variant="outline"
                  className="mt-3"
                  onClick={() => {
                    setSearch('')
                    FILTER_KEYS.forEach((key) => setFilter(key, ''))
                  }}
                >
                  Clear the filters
                </Button>
              </>
            ) : (
              <p>Nothing has happened in this workspace yet.</p>
            )}
          </div>
        ) : (
          <>
            <p aria-live="polite" className="mb-2 text-sm text-fg-muted">
              {lines.length === 1 ? '1 line' : `${NUMBER.format(lines.length)} lines`}
              {log.hasNextPage ? ', more below.' : '.'}
            </p>
            <Lines lines={lines} />
            {log.hasNextPage && (
              <Button
                type="button"
                variant="outline"
                className="mt-4"
                disabled={log.isFetchingNextPage}
                aria-busy={log.isFetchingNextPage}
                onClick={() => void log.fetchNextPage()}
              >
                {log.isFetchingNextPage && (
                  <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                )}
                {log.isFetchingNextPage ? 'Loading…' : 'Load more'}
              </Button>
            )}
          </>
        )}
      </div>
    </>
  )
}

function Filters({
  filters,
  setFilter,
  search,
  setSearch,
}: {
  filters: AuditFilters
  setFilter: (key: (typeof FILTER_KEYS)[number], value: string) => void
  search: string
  setSearch: (text: string) => void
}) {
  const ids = { action: useId(), outcome: useId(), email: useId(), q: useId() }
  const members = useMembers()

  // Someone removed since still has lines: keep their email in the list when it is the filter.
  const emails = members.data?.map((member) => member.email) ?? []
  if (filters.email && !emails.includes(filters.email)) emails.push(filters.email)

  return (
    <div
      role="search"
      aria-label="Filter the audit log"
      className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4"
    >
      <div>
        <label htmlFor={ids.action} className="text-sm font-medium">
          Action
        </label>
        <select
          id={ids.action}
          value={filters.action ?? ''}
          onChange={(event) => setFilter('action', event.target.value)}
          className={FIELD}
        >
          <option value="">Every action</option>
          {ACTIONS.map((action) => (
            <option key={action} value={action}>
              {ACTION_LABELS[action]}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={ids.outcome} className="text-sm font-medium">
          Outcome
        </label>
        <select
          id={ids.outcome}
          value={filters.outcome ?? ''}
          onChange={(event) => setFilter('outcome', event.target.value)}
          className={FIELD}
        >
          <option value="">Every outcome</option>
          {OUTCOME_GROUPS.map((group) => (
            <optgroup key={group.kind} label={group.label}>
              {group.values.map((value) => (
                <option key={value} value={value}>
                  {outcomeOf(value).label}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={ids.email} className="text-sm font-medium">
          Person
        </label>
        <select
          id={ids.email}
          value={filters.email ?? ''}
          onChange={(event) => setFilter('email', event.target.value)}
          className={FIELD}
        >
          <option value="">Everyone</option>
          {emails.map((email) => (
            <option key={email} value={email}>
              {email}
            </option>
          ))}
        </select>
      </div>
      <div>
        <label htmlFor={ids.q} className="text-sm font-medium">
          Find in questions and SQL
        </label>
        <input
          id={ids.q}
          type="search"
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
          className={FIELD}
        />
      </div>
    </div>
  )
}

function Lines({ lines }: { lines: AuditLine[] }) {
  const connections = useConnections()
  const names = new Map(connections.data?.map(({ id, name }) => [id, name]))
  const databaseName = (id: string | null | undefined) =>
    id ? (names.get(id) ?? 'A removed database') : DEMO_NAME

  return (
    <>
      {/* The columns' names, for the eyes on wide screens. Each row's button says it all itself. */}
      <div
        aria-hidden="true"
        className="hidden gap-3 px-3 pb-1 text-xs font-medium text-fg-muted sm:grid sm:grid-cols-[8.5rem_minmax(0,1fr)_9rem_minmax(0,12rem)_1rem]"
      >
        <span>Time</span>
        <span>Who</span>
        <span>What</span>
        <span>Outcome</span>
      </div>
      <ul aria-label="Audit log lines" className="divide-y rounded-cell border bg-surface">
        {lines.map((line) => (
          <Line key={line.id} line={line} databaseName={databaseName(line.connection_id)} />
        ))}
      </ul>
    </>
  )
}

function Line({ line, databaseName }: { line: AuditLine; databaseName: string }) {
  const [open, setOpen] = useState(false)
  const detailsId = useId()
  const outcome = outcomeOf(line.outcome)
  const summary = line.question ?? line.detail ?? '—'

  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={detailsId}
        onClick={() => setOpen(!open)}
        className={cn(
          'grid w-full grid-cols-[minmax(0,1fr)_1rem] items-start gap-x-3 gap-y-1 px-3 py-2.5 text-left text-sm',
          'sm:grid-cols-[8.5rem_minmax(0,1fr)_9rem_minmax(0,12rem)_1rem]',
          'outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring focus-visible:ring-inset',
        )}
      >
        {/* The spaces between the parts are text: a screen reader reads the row as one sentence. */}
        <time dateTime={line.at} className={cn(PART, 'text-fg-muted tabular-nums')}>
          {WHEN.format(new Date(line.at))}
        </time>{' '}
        <span className={cn(PART, 'truncate')}>{line.email ?? 'Unknown account'}</span>{' '}
        <span className={cn(PART, 'font-medium')}>{ACTION_LABELS[line.action]}</span>{' '}
        <Outcome kind={outcome.kind} label={outcome.label} />{' '}
        <span className={cn(PART, 'truncate text-fg-muted sm:col-span-3 sm:col-start-2')}>
          {summary}
        </span>
        <ChevronDown
          aria-hidden="true"
          className={cn(
            'col-start-2 row-start-1 mt-0.5 size-4 text-fg-muted transition-transform motion-reduce:transition-none sm:col-start-5',
            open && 'rotate-180',
          )}
        />
      </button>
      {open && (
        <div id={detailsId} className="border-t bg-muted/40 px-3 py-3 text-sm">
          <Details line={line} databaseName={databaseName} />
        </div>
      )}
    </li>
  )
}

// On a phone the row's parts stack in the first column (the chevron has the second); side by side
// from `sm`.
const PART = 'col-start-1 min-w-0 sm:col-start-auto'

const OUTCOME_ICONS: Record<OutcomeKind, { Icon: typeof CircleCheck; className: string }> = {
  done: { Icon: CircleCheck, className: 'text-signal' },
  declined: { Icon: CircleMinus, className: 'text-warn' },
  failed: { Icon: CircleX, className: 'text-danger' },
}

/** An icon and words: the colour only helps. */
function Outcome({ kind, label }: { kind: OutcomeKind; label: string }) {
  const { Icon, className } = OUTCOME_ICONS[kind]
  return (
    <span className={cn(PART, 'flex min-w-0 items-start gap-1.5')}>
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', className)} />
      <span className="min-w-0">{label}</span>
    </span>
  )
}

function Details({ line, databaseName }: { line: AuditLine; databaseName: string }) {
  const facts: [string, ReactNode][] = []
  // Questions ran on a database (null = the demo); onboarding and descriptions name theirs by id.
  // A saved database's other actions name it in the detail (user@host:port/db).
  if (line.action === 'ask' || line.action === 'rerun' || line.connection_id) {
    facts.push(['Database', databaseName])
  }
  if (line.question) facts.push(['Question', line.question])
  if (line.detail) facts.push([line.question ? 'Message' : 'Detail', line.detail])
  if (line.tables.length > 0) facts.push(['Tables', line.tables.join(', ')])
  if (line.row_count != null) {
    facts.push(['Rows', line.row_count === 1 ? '1 row' : `${NUMBER.format(line.row_count)} rows`])
  }
  if (line.duration_ms != null) facts.push(['Took', `${NUMBER.format(line.duration_ms)} ms`])
  if (line.sql_attempts != null && line.sql_attempts > 1) {
    facts.push(['SQL tries', String(line.sql_attempts)])
  }
  if (line.llm) {
    facts.push(['Model', `${line.llm}, ${line.own_key ? 'the workspace’s key' : 'the demo key'}`])
  }
  if (line.input_tokens != null && line.output_tokens != null) {
    facts.push([
      'Tokens',
      `${NUMBER.format(line.input_tokens)} in + ${NUMBER.format(line.output_tokens)} out`,
    ])
  }
  if (line.llm) {
    facts.push(['Cost', line.cost_usd != null ? DOLLARS.format(line.cost_usd) : 'price not known'])
  }

  return (
    <div className="space-y-3">
      <dl className="grid gap-x-4 gap-y-1.5 sm:grid-cols-[8rem_minmax(0,1fr)]">
        <dt className="text-fg-muted">When</dt>
        <dd>
          <time dateTime={line.at}>{new Date(line.at).toLocaleString('en')}</time>
        </dd>
        {facts.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-fg-muted">{term}</dt>
            <dd className="break-words">{value}</dd>
          </div>
        ))}
      </dl>
      {line.sql && (
        <div>
          <p className="mb-1.5 text-fg-muted">SQL</p>
          <SqlCode sql={line.sql} />
        </div>
      )}
    </div>
  )
}
