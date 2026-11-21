import { Pause, Play, RotateCcw } from 'lucide-react'
import { lazy, Suspense, useEffect, useState } from 'react'

import type { AskResponse } from '@/api/client'
import type { TraceEvent } from '@/api/stream'
import { AgentRail } from '@/canvas/AgentRail'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
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
 * The landing page's live demo (U8.4): a recorded question with a repair, replayed through the real
 * rail and the real answer card, one event at a time. Nothing is faked and nothing is sent: no API
 * call, no LLM. It plays once; Pause / Play / Replay control it (moving content longer than 5 s
 * needs a way to stop it, WCAG 2.2.2). Under reduced motion it shows the finished run at once.
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
    <section
      aria-labelledby={headingId}
      className="glow relative rounded-cell border border-transparent bg-surface p-4 text-left sm:p-6"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="label-mono flex items-center gap-2">
          <span
            aria-hidden="true"
            className={cn('size-1.5 rounded-full', done ? 'bg-input' : 'bg-signal')}
          />
          {/* Says plainly that this is a recording: the page sells nothing it can't do. */}
          Replay of a recorded run · no AI called
        </p>
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
      <h3 id={headingId} className="mb-4 text-lg font-semibold">
        {REPLY.question}
      </h3>
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
    </section>
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
