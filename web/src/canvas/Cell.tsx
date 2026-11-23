import { CornerDownRight } from 'lucide-react'
import { lazy, Suspense } from 'react'

import type { RerunResult } from '@/api/conversations'
import type { TraceEvent } from '@/api/stream'
import { ChannelBar } from '@/components/Instrument'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import { DrawPath, Reveal } from '@/motion'

import { AgentRail } from './AgentRail'
import { ClarificationCell } from './ClarificationCell'
import { ErrorCell } from './ErrorCell'
import { RefusalCell } from './RefusalCell'
import { SaveQuestionButton } from './SaveQuestionButton'
import type { Cell as CellData } from './state'

// Loaded with the first answer, not with the page: the tabs and tooltip it uses (Radix) then stay
// out of the first download, which only needs the examples and the composer.
const AnswerCell = lazy(() =>
  import('./AnswerCell').then(({ AnswerCell }) => ({ default: AnswerCell })),
)

const NO_EVENTS: readonly TraceEvent[] = []

type CellProps = {
  cell: CellData
  /** Its place in the conversation (0 = first), for the "READOUT 01" label. */
  index?: number
  /** The conversation's first question: its words make sense on their own. */
  first: boolean
  /** The question of the cell this one follows up on, if there is one. */
  parentQuestion: string | undefined
  /** A question is running in this conversation: anything that would ask another one waits. */
  busy: boolean
  /** Asks a question that continues from this cell. */
  onAsk: (question: string, parentId: string) => void
  /** Runs this failed cell's question again, in place. */
  onRetry: (cellId: string) => void
  /** Puts this cell's question back in the composer, to change it. */
  onRephrase: (question: string) => void
  /** Sets up the composer to follow up on this cell. */
  onFollowUp: (cellId: string) => void
  /** Stops this cell's question while it runs. */
  onStop: (cellId: string) => void
  /** Fresh rows arrived for this saved cell. */
  onRerun: (cellId: string, result: RerunResult) => void
}

/** The word in the entry's header for what came back (decoration: the body says it in full). */
function statusWord(cell: CellData): string {
  if (cell.status === 'running') return 'RUNNING'
  if (cell.status === 'failed' || cell.reply === undefined) return 'FAILED'
  switch (cell.reply.kind) {
    case 'answer':
      return 'ANSWER'
    case 'clarification':
      return 'QUESTION BACK'
    case 'refusal':
      return 'DECLINED'
  }
}

/**
 * One question on the canvas, and whatever came back for it: an entry in a lab notebook (U9.4,
 * ADR 027; a "readout" since U8.3). The frame is shared: a channel bar with the entry's number and
 * status, then the question as a command line. The body depends on the status and the reply's
 * `kind`. It rises into place when it is added, and glows softly while the agent works.
 */
export function Cell(props: CellProps) {
  const { cell, index, parentQuestion, onStop } = props
  const headingId = `${cell.id}-question`
  const isFollowUp = parentQuestion !== undefined
  const running = cell.status === 'running'
  return (
    <Reveal
      as="article"
      aria-labelledby={headingId}
      aria-describedby={isFollowUp ? `${cell.parentId}-question` : undefined}
      aria-busy={running}
      // @container: the answer inside lays out by the entry's own width (the readings panel goes
      // to the side from 48rem), not the window's, which also holds the menu and the history.
      // No overflow-hidden: the follow-up elbow is drawn just outside the top-left corner.
      className={cn(
        '@container relative rounded-cell border bg-surface transition-[box-shadow,border-color] duration-500',
        running && 'glow border-transparent',
        isFollowUp && 'ml-5 sm:ml-8',
      )}
    >
      {isFollowUp && (
        // Screen readers already hear the parent question, through aria-describedby above.
        <div aria-hidden="true">
          {/* An elbow from the question above into this one, drawn when the follow-up appears. */}
          <svg
            viewBox="0 0 20 32"
            className="absolute -top-4 -left-3.5 h-8 w-3 overflow-visible text-input sm:-left-6 sm:w-5"
            preserveAspectRatio="none"
          >
            <DrawPath
              d="M 1 0 L 1 22 Q 1 31 10 31 L 20 31"
              fill="none"
              stroke="currentColor"
              strokeWidth={1.5}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
          <p className="truncate px-4 pt-2 text-xs text-fg-muted sm:px-5">
            Following up on: {parentQuestion}
          </p>
        </div>
      )}
      {index !== undefined && (
        // The entry's header strip: its number, lit while it runs, and what came back. The same
        // top radius as the card, so the strip's colour doesn't poke out of the rounded corners.
        <div aria-hidden="true">
          <ChannelBar
            live={running}
            label={`READOUT ${String(index + 1).padStart(2, '0')}`}
            className={cn('px-4 sm:px-5', !isFollowUp && 'rounded-t-cell')}
          >
            <span className="label-mono">{statusWord(cell)}</span>
          </ChannelBar>
        </div>
      )}
      <div className="p-4 sm:p-5">
        {/* The question as a command line: a prompt, then the words as typed (mono). The prompt
            is outside the heading, so the entry is still named by the question alone. */}
        <div className="mb-4 flex items-baseline gap-3">
          <span aria-hidden="true" className="shrink-0 font-mono text-sm font-semibold text-signal">
            qm&gt;
          </span>
          <h2
            id={headingId}
            className="min-w-0 font-mono text-base leading-snug font-medium wrap-break-word sm:text-lg"
          >
            {cell.question}
          </h2>
        </div>
        {/* Not after a fallback to POST /ask, which sends no steps: the answer is complete anyway. */}
        {(cell.status === 'running' || cell.trace !== undefined) && (
          <AgentRail
            trace={cell.trace ?? NO_EVENTS}
            status={cell.status}
            stopped={cell.error?.code === 'stopped'}
            onStop={() => onStop(cell.id)}
            describedBy={headingId}
          />
        )}
        <CellBody {...props} />
      </div>
    </Reveal>
  )
}

function CellBody({
  cell,
  first,
  busy,
  onAsk,
  onRetry,
  onRephrase,
  onFollowUp,
  onRerun,
}: CellProps) {
  if (cell.status === 'running') return <RunningBody />
  if (cell.status === 'failed' || cell.reply === undefined) {
    return (
      <ErrorCell
        error={cell.error ?? { code: 'unexpected', message: '' }}
        busy={busy}
        onRetry={() => onRetry(cell.id)}
        onRephrase={() => onRephrase(cell.question)}
      />
    )
  }

  const { reply } = cell
  switch (reply.kind) {
    case 'answer': {
      // "Now only for 1997" means nothing in a new conversation, which is where a saved question is
      // asked. So a later question can only be saved if the agent rewrote it to stand alone.
      const standalone = reply.search_question?.trim() || (first ? cell.question : null)
      return (
        <Suspense fallback={<RunningBody />}>
          <AnswerCell
            reply={reply}
            saved={cell.saved}
            onRerun={(result) => onRerun(cell.id, result)}
            actions={
              <>
                {/* Every cell has this button, so the description says which question it is for. */}
                <Button
                  type="button"
                  variant="outline"
                  aria-describedby={`${cell.id}-question`}
                  onClick={() => onFollowUp(cell.id)}
                >
                  <CornerDownRight aria-hidden="true" />
                  Follow up
                </Button>
                {standalone !== null && (
                  <SaveQuestionButton cellId={cell.id} question={standalone} />
                )}
              </>
            }
          />
        </Suspense>
      )
    }
    case 'clarification':
      return (
        <ClarificationCell
          reply={reply}
          busy={busy}
          onAnswer={(question) => onAsk(question, cell.id)}
        />
      )
    case 'refusal':
      return <RefusalCell reply={reply} />
  }
}

/** The answer's shape in grey while the agent works (no layout jump when it arrives). */
function RunningBody() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-4 w-3/4 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-1/2 motion-reduce:animate-none" />
      <Skeleton className="h-24 w-full motion-reduce:animate-none" />
    </div>
  )
}
