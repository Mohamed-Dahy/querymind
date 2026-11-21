import { useQueryClient } from '@tanstack/react-query'
import { Check, CircleAlert, Loader2, Sparkles } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'
import { Link } from 'react-router'

import { asApiError, type ApiError } from '@/api/client'
import {
  isRunning,
  onboardingKey,
  startOnboarding,
  useOnboarding,
  type OnboardingInfo,
  type OnboardingState,
} from '@/api/onboarding'
import type { ConnectionInfo } from '@/api/settings'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { FormError } from './FormError'
import { reviewPath } from './tabs'
import { RetryNote } from './RetryNote'

const WHEN = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' })

// The stages a run goes through (store/onboarding.py), in order. "queued" comes before the first.
const STAGES = [
  { status: 'reading', label: 'Reading tables' },
  { status: 'drafting', label: 'Drafting descriptions' },
  { status: 'ready', label: 'Ready' },
] as const satisfies readonly { status: OnboardingState; label: string }[]

/** What a screen reader hears when the stage changes (not on every poll). */
const STAGE_WORDS: Record<OnboardingState, string> = {
  queued: 'onboarding is about to start',
  reading: 'reading its tables',
  drafting: 'drafting descriptions',
  ready: 'onboarded',
  failed: 'onboarding failed',
}

/**
 * One database's onboarding (U7.1c), on its row in Settings → Databases, for admins: not onboarded
 * yet (with "Onboard" once the workspace has its own key), the stages and counts while a run goes
 * on (polled), then "Review descriptions". Saving a database with a key already starts a run.
 */
export function OnboardingStatus({
  connection,
  hasKey,
}: {
  connection: ConnectionInfo
  /** Has the workspace its own LLM key? `undefined` while that is loading. */
  hasKey: boolean | undefined
}) {
  const queryClient = useQueryClient()
  const onboarding = useOnboarding(connection.id)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number>()
  const errorId = useId()

  // Announced only when the stage changes in front of the user, not for each row on page load.
  // `undefined` while loading, `null` never onboarded: the first answer isn't a change.
  const known = onboarding.data === undefined ? undefined : (onboarding.data?.status ?? null)
  const [seen, setSeen] = useState(known)
  const [announced, setAnnounced] = useState('')
  if (known !== seen) {
    setSeen(known)
    if (seen !== undefined && known != null) {
      setAnnounced(`${connection.name}: ${STAGE_WORDS[known]}.`)
    }
  }

  async function start() {
    setStarting(true)
    setError(null)
    try {
      const info = await startOnboarding(connection.id)
      queryClient.setQueryData(onboardingKey(connection.id), info)
    } catch (caught) {
      const failure = asApiError(caught)
      setError(failure)
      if (failure.retryAfterSeconds !== undefined) {
        setRetryAt(Date.now() + failure.retryAfterSeconds * 1000)
      }
      // Started meanwhile (another tab, another admin): show that run.
      if (failure.code === 'onboarding_running') void onboarding.refetch()
    } finally {
      setStarting(false)
    }
  }

  const startButton = (label: string) => (
    <Button
      type="button"
      variant="outline"
      disabled={starting}
      aria-busy={starting}
      aria-describedby={error ? errorId : undefined}
      className="pointer-coarse:min-h-11"
      onClick={() => void start()}
    >
      {starting ? (
        <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
      ) : (
        <Sparkles aria-hidden="true" />
      )}
      {starting ? 'Starting…' : label} <span className="sr-only">{connection.name}</span>
    </Button>
  )

  return (
    <div className="mt-4 border-t pt-4 text-sm">
      <p role="status" className="sr-only">
        {announced}
      </p>
      {onboarding.isPending ? (
        <Skeleton aria-hidden="true" className="h-5 w-56 motion-reduce:animate-none" />
      ) : onboarding.isError ? (
        <p className="text-fg-muted">
          Onboarding progress can&rsquo;t be shown right now.{' '}
          <button
            type="button"
            className="text-info underline underline-offset-4"
            onClick={() => void onboarding.refetch()}
          >
            Try again
          </button>
        </p>
      ) : onboarding.data === null ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 text-fg-muted">
            <span className="font-medium text-fg">Not onboarded yet.</span> Onboarding drafts a
            description of each table with your LLM key, so answers know what your data means.
          </p>
          {hasKey === true && startButton('Onboard')}
          {hasKey === false && (
            <p className="text-fg-muted">
              It needs your own LLM key:{' '}
              <Link to="/settings/key" className="text-info underline underline-offset-4">
                save one first
              </Link>
              .
            </p>
          )}
        </div>
      ) : isRunning(onboarding.data.status) ? (
        <Progress info={onboarding.data} />
      ) : (
        <Finished info={onboarding.data} connection={connection} startButton={startButton} />
      )}
      {error && (
        <div className="mt-3">
          <FormError id={errorId} error={error} />
          {retryAt !== undefined && <RetryNote key={retryAt} retryAt={retryAt} action="onboard" />}
        </div>
      )}
    </div>
  )
}

/** A run going on: the three stages, and how many tables are drafted. */
function Progress({ info }: { info: OnboardingInfo }) {
  const current = STAGES.findIndex(({ status }) => status === info.status) // -1 while queued
  const done = info.tables_drafted + info.tables_skipped
  return (
    <div className="space-y-2">
      <ol aria-label="Onboarding" className="flex flex-wrap gap-x-4 gap-y-1">
        {STAGES.map(({ status, label }, index) => {
          const state = index < current ? 'done' : index === current ? 'current' : 'todo'
          return (
            <li
              key={status}
              aria-current={state === 'current' ? 'step' : undefined}
              className={cn(
                'flex items-center gap-1.5',
                state === 'todo' ? 'text-fg-muted' : 'text-fg',
                state === 'current' && 'font-medium',
                // The stage running now: a lime pill around it (U8.7).
                state === 'current' && '-mx-2 rounded-full bg-muted px-2 py-0.5',
              )}
            >
              {state === 'done' ? (
                <Check aria-hidden="true" className="size-4 text-signal" />
              ) : state === 'current' ? (
                <Loader2
                  aria-hidden="true"
                  className="size-4 animate-spin motion-reduce:animate-none"
                />
              ) : (
                <span
                  aria-hidden="true"
                  className="size-4 rounded-full border-2 border-current opacity-50"
                />
              )}
              {label}
              {state === 'done' && (
                <>
                  {' '}
                  <span className="sr-only">(done)</span>
                </>
              )}
            </li>
          )
        })}
      </ol>
      {info.status === 'queued' && <p className="text-fg-muted">Waiting to start…</p>}
      {info.status === 'drafting' && info.tables_total > 0 && (
        <>
          <div
            role="progressbar"
            aria-label="Tables drafted"
            aria-valuemin={0}
            aria-valuemax={info.tables_total}
            aria-valuenow={done}
            className="h-1.5 w-full max-w-sm overflow-hidden rounded-full bg-muted"
          >
            {/* Full width, scaled down to the share drafted (U8.7): moving `scale` instead of
                `width` changes no layout, so each new count only redraws the bar. */}
            <div
              className="h-full w-full origin-left rounded-full bg-signal transition-[scale] duration-500 ease-out-expo motion-reduce:transition-none"
              style={{ scale: `${done / info.tables_total} 1` }}
            />
          </div>
          <p className="text-fg-muted tabular-nums">
            {info.tables_drafted} of {info.tables_total} tables drafted
            {info.tables_skipped > 0 && ` · ${info.tables_skipped} skipped`}
          </p>
        </>
      )}
    </div>
  )
}

/** A run that ended: ready (with the review link), or failed (with why, and a way to retry). */
function Finished({
  info,
  connection,
  startButton,
}: {
  info: OnboardingInfo
  connection: ConnectionInfo
  startButton: (label: string) => ReactNode
}) {
  const review = info.has_dictionary && (
    <Link
      to={reviewPath(connection.id)}
      className="inline-flex min-h-8 items-center rounded-lg bg-primary px-2.5 font-medium text-primary-foreground hover:bg-primary/80 pointer-coarse:min-h-11"
    >
      Review descriptions <span className="sr-only">of {connection.name}</span>
    </Link>
  )
  return (
    <div className="space-y-3">
      {info.status === 'failed' ? (
        <p className="flex items-start gap-1.5">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
          <span>
            <span className="font-medium">Onboarding failed.</span>{' '}
            <span className="text-fg-muted">{info.error ?? 'Please try again.'}</span>
          </span>
        </p>
      ) : (
        <p className="flex items-start gap-1.5">
          <Check aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-signal" />
          <span>
            <span className="font-medium">Onboarded</span>
            <span className="text-fg-muted">
              , <time dateTime={info.updated_at}>{WHEN.format(new Date(info.updated_at))}</time>.
              {/* A run drafts only what has no description yet: a second one may find nothing. */}{' '}
              {lastRun(info)}
            </span>
          </span>
        </p>
      )}
      {info.status === 'ready' && info.tables_skipped > 0 && (
        <p className="text-xs text-fg-muted">
          Skipped: tables whose names aren&rsquo;t plain lowercase, past the limit one run covers,
          or whose draft didn&rsquo;t fit.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {review}
        {/* A new run keeps reviewed tables as they are and fills the gaps (new tables too). */}
        {startButton(info.status === 'failed' ? 'Try again' : 'Onboard again')}
      </div>
    </div>
  )
}

/** What the last run did: "Last run: 12 tables drafted · 1 skipped." */
function lastRun({ tables_drafted: drafted, tables_skipped: skipped }: OnboardingInfo): string {
  if (drafted === 0 && skipped === 0) return 'Last run: nothing new to draft.'
  const parts = [`${drafted} ${drafted === 1 ? 'table' : 'tables'} drafted`]
  if (skipped > 0) parts.push(`${skipped} skipped`)
  return `Last run: ${parts.join(' · ')}.`
}
