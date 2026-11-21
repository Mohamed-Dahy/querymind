import {
  Check,
  Circle,
  CircleSlash,
  LoaderCircle,
  CircleDashed,
  Square,
  X,
  type LucideIcon,
} from 'lucide-react'
import { m } from 'motion/react'
import { useMemo } from 'react'

import type { TraceEvent } from '@/api/stream'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { DrawPath, duration, ease, useMotionSafe } from '@/motion'

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

// An icon and words for each state, never colour alone. `node` is the look of the step's dot on
// the signal line (Signal Lab, U8.3): lime when lit, coral when it failed, dashed when skipped.
const STATES: Record<
  RailStepState,
  { Icon: LucideIcon; colour: string; node: string; words: string }
> = {
  waiting: {
    Icon: Circle,
    colour: 'text-fg-muted',
    node: 'border-border bg-surface',
    words: 'waiting',
  },
  running: {
    Icon: LoaderCircle,
    colour: 'text-signal',
    node: 'glow border-signal bg-surface',
    words: 'running',
  },
  done: { Icon: Check, colour: 'text-signal', node: 'border-signal/60 bg-muted', words: 'done' },
  failed: { Icon: X, colour: 'text-danger', node: 'border-danger bg-muted', words: 'failed' },
  skipped: {
    Icon: CircleDashed,
    colour: 'text-fg-muted',
    node: 'border-dashed border-border bg-surface',
    words: 'skipped',
  },
  stopped: {
    Icon: CircleSlash,
    colour: 'text-fg-muted',
    node: 'border-input bg-surface',
    words: 'stopped',
  },
}

/** A step the signal has reached: the line into it is lit. */
const REACHED: ReadonlySet<RailStepState> = new Set(['running', 'done', 'failed', 'stopped'])

type AgentRailProps = {
  trace: readonly TraceEvent[]
  status: CellStatus
  /** The user stopped this run, so what was running shows as stopped, not failed. */
  stopped: boolean
  /** Only needed while it runs. */
  onStop: () => void
  /** The heading of the cell, to say which question Stop is for. */
  describedBy: string
  /** False for a replay (the landing page's demo): there is no run to stop. */
  canStop?: boolean
}

/**
 * The agent's steps, live, as a signal line (U8.3): each real `step` event lights its node, the
 * line fills up to the step at work, a failed try flashes its node and the repair arc draws itself.
 * What is running, what is done and how long it took, and any repair, in words too.
 */
export function AgentRail({
  trace,
  status,
  stopped,
  onStop,
  describedBy,
  canStop = true,
}: AgentRailProps) {
  const ended = status !== 'running'
  const { steps, tries, repairs } = useMemo(
    () => railFrom(trace, { ended, stopped }),
    [trace, ended, stopped],
  )
  const failures = tries.filter((one) => one.failed !== undefined)
  const totalMs = RAIL_STEPS.reduce((sum, id) => sum + steps[id].durationMs, 0)

  return (
    <div className="mb-4">
      {/* On a phone Stop comes first, where the sticky composer can't cover it. */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="relative min-w-0 grow basis-full sm:basis-0">
          {/* Counted from the repairs: while one runs, its SQL try is not written yet. */}
          {repairs > 0 && <RepairArc tries={repairs + 1} />}
          <ol
            aria-label="Agent steps"
            className={cn(
              'grid grid-cols-2 gap-x-2 gap-y-3 min-[480px]:grid-cols-4 sm:grid-cols-7 sm:gap-x-0',
              repairs > 0 && 'sm:pt-7',
            )}
          >
            {RAIL_STEPS.map((id, index) => (
              <RailItem key={id} id={id} step={steps[id]} index={index} />
            ))}
          </ol>
        </div>
        {!ended && canStop && (
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

      {/* The run in one line once it is over: the same facts as the steps, so hidden from screen
          readers (they hear each step's time above). */}
      {ended && totalMs > 0 && (
        <p aria-hidden="true" className="label-mono mt-3">
          {`TRACE · ${formatDuration(totalMs)}${repairs > 0 ? ` · ${repairs} REPAIR${repairs > 1 ? 'S' : ''}` : ''}`}
        </p>
      )}

      {failures.length > 0 && (
        <ul
          aria-label="SQL tries that failed"
          className="mt-3 space-y-1 border-l-2 border-danger/60 pl-3 text-sm text-fg-muted"
        >
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

function RailItem({ id, step, index }: { id: RailStepId; step: RailStep; index: number }) {
  const { state, durationMs } = step
  const { Icon, colour, node, words } = STATES[state]
  const time = durationMs > 0 ? formatDuration(durationMs) : null

  // The markup the tests read stays as before: the step's text starts with its name, the name is the
  // first span inside a span, and the first svg is the state icon. So the node and the line are
  // divs, and the step number comes from CSS (not part of the text, and silent for screen readers).
  return (
    <li
      aria-current={state === 'running' ? 'step' : undefined}
      className="relative flex items-start gap-2 sm:flex-col sm:items-center sm:gap-1.5 sm:text-center"
    >
      {index > 0 && <Connector lit={REACHED.has(state)} failed={state === 'failed'} />}
      <div
        className={cn(
          'relative z-10 grid size-7 shrink-0 place-items-center rounded-full border transition-[border-color,background-color,box-shadow] duration-300',
          node,
        )}
      >
        {/* The step at work: a ring that keeps spreading out, like a beacon (a loading sign, so it
            may loop). Gone under reduced motion, where the words say "running". */}
        {state === 'running' && (
          <div
            aria-hidden="true"
            className="absolute inset-0 animate-ping rounded-full border border-signal motion-reduce:hidden"
          />
        )}
        {state === 'failed' && <FailFlash />}
        <Icon
          aria-hidden="true"
          className={cn(
            'size-3.5 transition-colors motion-reduce:transition-none',
            colour,
            // Under reduced motion a still icon: the words below say it is running.
            state === 'running' && 'animate-spin motion-reduce:animate-none',
          )}
        />
      </div>
      <span className="min-w-0 text-xs">
        <span
          data-step={String(index + 1).padStart(2, '0')}
          className={cn(
            'block',
            // "01", "02"… above the name: generated by CSS with empty alt text (after the slash),
            // so it is not read out and not in the text the tests check.
            "sm:before:block sm:before:font-mono sm:before:text-[0.625rem] sm:before:tracking-[0.08em] sm:before:text-fg-muted sm:before:content-[attr(data-step)_/_'']",
            state === 'running' || state === 'done' ? 'font-medium text-fg' : 'text-fg-muted',
          )}
        >
          {LABELS[id]}
        </span>
        <span className="block font-mono text-[0.6875rem] text-fg-muted tabular-nums">
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
 * The line from the step before to this one (seven-column row only). A hairline, with a lime fill
 * that grows along it (scaleX, from the left) when the signal reaches this step: the "pulse"
 * travelling to the step now at work. Coral when this step failed.
 */
function Connector({ lit, failed }: { lit: boolean; failed: boolean }) {
  const { transition } = useMotionSafe()
  return (
    <div
      aria-hidden="true"
      className="absolute top-3.5 -left-1/2 hidden h-px w-full overflow-hidden bg-border sm:block"
    >
      <m.div
        className={cn('h-full origin-left', failed ? 'bg-danger' : 'bg-signal')}
        // initial={false}: a saved answer's line is drawn already; only a live change animates.
        initial={false}
        animate={{ scaleX: lit ? 1 : 0 }}
        transition={transition({ duration: duration.slow * 1.5, ease: ease.out })}
      />
    </div>
  )
}

/** A failed try: one coral ring bursting out of the node, once. Nothing under reduced motion. */
function FailFlash() {
  const { animate } = useMotionSafe()
  if (!animate) return null
  return (
    <m.div
      aria-hidden="true"
      className="absolute inset-0 rounded-full border-2 border-danger"
      initial={{ scale: 1, opacity: 0.9 }}
      animate={{ scale: 2.2, opacity: 0 }}
      transition={{ duration: 0.6, ease: ease.out }}
    />
  )
}

/**
 * The repair loop: from Repair back to Validate, because every repaired query is validated again.
 * It draws itself when the first repair starts. Over the columns of the seven-column row, so it is
 * only drawn there. Screen readers, and people on a phone, get the same fact from the list of
 * failed tries.
 */
function RepairArc({ tries }: { tries: number }) {
  return (
    <div
      aria-hidden="true"
      className="absolute top-0 left-[35.7%] hidden h-7 w-[42.9%] text-warn sm:block"
    >
      <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="size-full overflow-visible">
        <DrawPath
          d="M 100 24 C 100 4, 100 2, 92 2 L 8 2 C 0 2, 0 4, 0 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      {/* The arrow head, where the loop lands on Validate. */}
      <span className="absolute bottom-0 -left-1 size-0 border-x-4 border-t-6 border-x-transparent border-t-current" />
      <span className="absolute -top-1.5 left-1/2 -translate-x-1/2 rounded-full border border-warn/40 bg-surface px-2 font-mono text-[0.6875rem]">
        {tries} tries
      </span>
    </div>
  )
}
