import { Pause, Play, RotateCcw } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'

import type { AskResponse } from '@/api/client'
import type { TraceEvent } from '@/api/stream'
import { AgentRail } from '@/canvas/AgentRail'
import { ChannelBar, CropMarks } from '@/components/Instrument'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useMotionSafe } from '@/motion'
// One real run, recorded from the real pipeline and database (scripts/record_fixtures.py): the
// events POST /ask/stream sent, then the reply. Only these two small files, not the whole fake API.
import recordedReply from '@/mocks/recorded/repair.json'
import recordedEvents from '@/mocks/recorded/streams/repair.json'

const EVENTS = recordedEvents as TraceEvent[]
const REPLY = recordedReply as AskResponse

/** Time between two replayed events: slow enough to follow each step, fast enough to finish. */
const STEP_MS = 420

// The real answer card, loaded when the replay reaches its answer (it brings the tabs and table).
const AnswerCell = lazy(() =>
  import('@/canvas/AnswerCell').then(({ AnswerCell }) => ({ default: AnswerCell })),
)

/**
 * The landing page's live demo (U8.4, an instrument screen since U9.1): a recorded question with a
 * repair, replayed through the real rail and the real answer card, one event at a time. Nothing is
 * faked and nothing is sent: no API call, no LLM. It plays once; Pause / Play / Replay control it
 * (moving content longer than 5 s needs a way to stop it, WCAG 2.2.2). Under reduced motion it shows
 * the finished run at once.
 */
export function LiveDemo() {
  const { animate } = useMotionSafe()
  const [shown, setShown] = useState(0)
  const [paused, setPaused] = useState(false)
  // Reduced motion (or unit tests): the whole run, no replay.
  const visible = animate ? shown : EVENTS.length
  const done = visible >= EVENTS.length

  useEffect(() => {
    if (!animate || paused || done) return undefined
    const timer = setTimeout(() => setShown((count) => count + 1), STEP_MS)
    return () => clearTimeout(timer)
  }, [animate, paused, done, shown])

  const headingId = 'demo-question'
  return (
    <section aria-labelledby={headingId} className="relative text-left">
      <CropMarks />
      <div className="overflow-hidden rounded-[6px] border bg-surface shadow-2xl shadow-black/30">
        <ChannelBar
          live={!done}
          // Says plainly that this is a recording: the page sells nothing it can't do.
          label={
            <>
              <span className="text-fg">CH-1</span>
              <span className="truncate">Replay of a recorded run · no AI called</span>
            </>
          }
        >
          <div className="flex items-center gap-3">
            {/* How far the replay is: real events of the recorded stream, counted. */}
            <span aria-hidden="true" className="label-mono tabular-nums">
              {String(visible).padStart(2, '0')}/{EVENTS.length} EVENTS
            </span>
            {animate && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                aria-pressed={done ? undefined : paused}
                onClick={() => {
                  if (done) {
                    setShown(0)
                    setPaused(false)
                  } else setPaused((was) => !was)
                }}
              >
                {done ? (
                  <>
                    <RotateCcw aria-hidden="true" />
                    Replay
                  </>
                ) : paused ? (
                  <>
                    <Play aria-hidden="true" />
                    Play
                  </>
                ) : (
                  <>
                    <Pause aria-hidden="true" />
                    Pause
                  </>
                )}
              </Button>
            )}
          </div>
        </ChannelBar>
        <Scope events={EVENTS} visible={visible} />
        <div className="p-4 sm:p-5">
          <p aria-hidden="true" className="label-mono">
            INPUT
          </p>
          {/* h2: the demo is a section right under the page's h1 (Lighthouse heading-order). */}
          <h2 id={headingId} className="mt-1 mb-4 font-mono text-base font-medium">
            <span aria-hidden="true" className="text-signal">
              &gt;{' '}
            </span>
            {REPLY.question}
          </h2>
          <AgentRail
            trace={EVENTS.slice(0, visible)}
            status={done ? 'done' : 'running'}
            stopped={false}
            onStop={() => setPaused(true)}
            describedBy={headingId}
            canStop={false}
          />
          {/* Room kept for the answer, so nothing below jumps when it arrives or the replay restarts. */}
          <div className="min-h-[22rem]">
            {done ? (
              <Suspense fallback={<DemoSkeleton />}>
                <AnswerCell reply={REPLY} />
              </Suspense>
            ) : (
              <DemoSkeleton />
            )}
          </div>
        </div>
      </div>
    </section>
  )
}

/** How tall each kind of event draws on the scope (0 = the base line, 1 = the top). */
function heightOf(event: TraceEvent): number {
  switch (event.type) {
    case 'step':
      return event.status === 'started' ? 0.85 : 0.15
    case 'sql':
      return 0.65
    case 'attempt_failed':
      return -0.8 // the failed try dips below the line
    default:
      return 0.4
  }
}

const W = 600
const H = 64
const BASE = 40

/**
 * An oscilloscope trace of the replay: one blip per real event of the recorded stream, drawn as it
 * arrives, so the line grows with the run. The failed try dips below the base line and is marked in
 * the warning colour. Decoration (the rail below says the same in words), so hidden from screen
 * readers; under reduced motion it is simply the finished trace.
 */
function Scope({ events, visible }: { events: TraceEvent[]; visible: number }) {
  const step = (W - 24) / Math.max(events.length - 1, 1)
  const x = (index: number) => 12 + index * step
  const y = (height: number) => BASE - height * (height > 0 ? 28 : 20)
  let path = `M 0 ${BASE}`
  events.slice(0, visible).forEach((event, index) => {
    path += ` L ${x(index) - 5} ${BASE} L ${x(index)} ${y(heightOf(event))} L ${x(index) + 5} ${BASE}`
  })
  const failed = events.findIndex(({ type }) => type === 'attempt_failed')
  const failedEvent = events[failed]
  const end = visible > 0 ? x(visible - 1) + 5 : 0
  return (
    <svg
      aria-hidden="true"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      className="block h-16 w-full border-b bg-bg text-signal"
    >
      {/* The scope's graticule: a dashed base line and a tick under every event to come. */}
      <line
        x1="0"
        x2={W}
        y1={BASE}
        y2={BASE}
        stroke="var(--border)"
        strokeDasharray="3 4"
        vectorEffect="non-scaling-stroke"
      />
      {events.map((_, index) => (
        <line
          key={index}
          x1={x(index)}
          x2={x(index)}
          y1={H - 6}
          y2={H}
          stroke="var(--border)"
          vectorEffect="non-scaling-stroke"
        />
      ))}
      <path
        d={path}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinejoin="round"
        vectorEffect="non-scaling-stroke"
      />
      {failedEvent !== undefined && failed < visible && (
        <circle cx={x(failed)} cy={y(heightOf(failedEvent))} r="3" fill="var(--warn)" />
      )}
      {/* The pen: where the trace is now. */}
      <line
        x1={end}
        x2={end}
        y1="0"
        y2={H}
        stroke="currentColor"
        strokeOpacity={visible < events.length ? 0.5 : 0}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

function DemoSkeleton() {
  return (
    <div aria-hidden="true" className="space-y-2">
      <Skeleton className="h-5 w-3/4 motion-reduce:animate-none" />
      <Skeleton className="h-10 w-full motion-reduce:animate-none" />
      <Skeleton className="h-48 w-full motion-reduce:animate-none" />
    </div>
  )
}
