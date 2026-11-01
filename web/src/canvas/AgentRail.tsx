import {
  Check,
  Circle,
  CircleSlash,
  LoaderCircle,
  Minus,
  Square,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useMemo } from 'react'

import type { TraceEvent } from '@/api/stream'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import type { CellStatus } from './state'
import {
  formatDuration,
  railFrom,
  RAIL_STEPS,
  type RailStep,
  type RailStepId,
  type RailStepState,
} from './trace'

const LABELS: Record<RailStepId, string> = {
  retrieve: 'Retrieve',
  write: 'Write SQL',
  validate: 'Validate',
  run: 'Run',
  check: 'Check',
  repair: 'Repair',
  summarize: 'Summarize',
}

// An icon and words for each state, never colour alone.
const STATES: Record<RailStepState, { Icon: LucideIcon; colour: string; words: string }> = {
  waiting: { Icon: Circle, colour: 'text-fg-muted', words: 'waiting' },
  running: { Icon: LoaderCircle, colour: 'text-info', words: 'running' },
  done: { Icon: Check, colour: 'text-signal', words: 'done' },
  failed: { Icon: X, colour: 'text-danger', words: 'failed' },
  skipped: { Icon: Minus, colour: 'text-fg-muted', words: 'skipped' },
  stopped: { Icon: CircleSlash, colour: 'text-fg-muted', words: 'stopped' },
}

type AgentRailProps = {
  trace: readonly TraceEvent[]
  status: CellStatus
  /** The user stopped this run, so what was running shows as stopped, not failed. */
  stopped: boolean
  /** Only needed while it runs. */
  onStop: () => void
  /** The heading of the cell, to say which question Stop is for. */
  describedBy: string
}

/** The agent's steps, live: what is running, what is done and how long it took, and any repair. */
export function AgentRail({ trace, status, stopped, onStop, describedBy }: AgentRailProps) {
  const ended = status !== 'running'
  const { steps, tries, repairs } = useMemo(
    () => railFrom(trace, { ended, stopped }),
    [trace, ended, stopped],
  )
  const failures = tries.filter((one) => one.failed !== undefined)

  return (
    <div className="mb-3">
      {/* On a phone Stop comes first, where the sticky composer can't cover it. */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="relative min-w-0 grow basis-full sm:basis-0">
          {/* Counted from the repairs: while one runs, its SQL try is not written yet. */}
          {repairs > 0 && <RepairArc tries={repairs + 1} />}
          <ol
            aria-label="Agent steps"
            className={cn(
              'grid grid-cols-2 gap-x-2 gap-y-2 min-[480px]:grid-cols-4 sm:grid-cols-7 sm:gap-x-0',
              repairs > 0 && 'sm:pt-6',
            )}
          >
            {RAIL_STEPS.map((id, index) => (
              <RailItem key={id} id={id} step={steps[id]} first={index === 0} />
            ))}
          </ol>
        </div>
        {!ended && (
          <Button
            type="button"
            variant="outline"
            aria-describedby={describedBy}
            className="order-first sm:order-last"
            onClick={onStop}
          >
            <Square aria-hidden="true" />
            Stop
          </Button>
        )}
      </div>

      {failures.length > 0 && (
        <ul aria-label="SQL tries that failed" className="mt-2 space-y-1 text-sm text-fg-muted">
          {failures.map(({ attempt, failed }) => (
            <li key={attempt}>
              {failed?.kind === 'suspicious'
                ? `Try ${attempt} looked wrong: `
                : `Try ${attempt} failed: `}
              {failed?.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function RailItem({ id, step, first }: { id: RailStepId; step: RailStep; first: boolean }) {
  const { state, durationMs } = step
  const { Icon, colour, words } = STATES[state]
  const time = durationMs > 0 ? formatDuration(durationMs) : null

  return (
    <li
      aria-current={state === 'running' ? 'step' : undefined}
      className="relative flex items-start gap-1.5 sm:flex-col sm:items-center sm:gap-0.5 sm:text-center"
    >
      {/* The line to the step before. Behind the icon, which has the cell's colour around it. */}
      {!first && (
        <span
          aria-hidden="true"
          className="absolute top-2 -left-1/2 hidden h-px w-full bg-border sm:block"
        />
      )}
      <span className="relative mt-0.5 rounded-full bg-surface sm:mt-0 sm:px-1">
        <Icon
          aria-hidden="true"
          className={cn(
            'size-4 transition-colors motion-reduce:transition-none',
            colour,
            // Under reduced motion a still icon: the words below say it is running.
            state === 'running' && 'animate-spin motion-reduce:animate-none',
          )}
        />
      </span>
      <span className="min-w-0 text-xs">
        <span className={cn('block', state === 'running' && 'font-medium')}>{LABELS[id]}</span>
        <span className="block text-fg-muted tabular-nums">
          {time && state === 'done' ? (
            <>
              <span className="sr-only">done, </span>
              {time}
            </>
          ) : (
            <>
              {words}
              {time && `, ${time}`}
            </>
          )}
        </span>
      </span>
    </li>
  )
}

/**
 * The repair loop: from Repair back to Validate, because every repaired query is validated again.
 * Over the columns of the seven-column row, so it is only drawn there. Screen readers, and people on
 * a phone, get the same fact from the list of failed tries.
 */
function RepairArc({ tries }: { tries: number }) {
  return (
    <div
      aria-hidden="true"
      className="absolute top-0 left-[35.7%] hidden h-6 w-[42.9%] text-info sm:block"
    >
      <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="size-full overflow-visible">
        <path
          d="M 100 24 C 100 4, 100 2, 92 2 L 8 2 C 0 2, 0 4, 0 24"
          fill="none"
          stroke="currentColor"
          strokeDasharray="4 3"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {/* The arrow head, where the loop lands on Validate. */}
      <span className="absolute bottom-0 -left-1 size-0 border-x-4 border-t-6 border-x-transparent border-t-current" />
      <span className="absolute -top-1 left-1/2 -translate-x-1/2 bg-surface px-1.5 text-xs">
        {tries} tries
      </span>
    </div>
  )
}
