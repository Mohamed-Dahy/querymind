import { lazy, Suspense } from 'react'

import { Skeleton } from '@/components/ui/skeleton'

import { ClarificationCell } from './ClarificationCell'
import { ErrorCell } from './ErrorCell'
import { RefusalCell } from './RefusalCell'
import type { Cell as CellData } from './state'

// Loaded with the first answer, not with the page: the tabs and tooltip it uses (Radix) then stay
// out of the first download, which only needs the examples and the composer.
const AnswerCell = lazy(() =>
  import('./AnswerCell').then(({ AnswerCell }) => ({ default: AnswerCell })),
)

type CellProps = {
  cell: CellData
  /** A question is running in this conversation: anything that would ask another one waits. */
  busy: boolean
  /** Asks a question that continues from this cell. */
  onAsk: (question: string, parentId: string) => void
  /** Runs this failed cell's question again, in place. */
  onRetry: (cellId: string) => void
  /** Puts this cell's question back in the composer, to change it. */
  onRephrase: (question: string) => void
}

/**
 * One question on the canvas, and whatever came back for it. The frame (the question as a heading)
 * is shared; the body depends on the status and the reply's `kind`.
 */
export function Cell(props: CellProps) {
  const { cell } = props
  const headingId = `${cell.id}-question`
  return (
    <article
      aria-labelledby={headingId}
      aria-busy={cell.status === 'running'}
      className="rounded-cell border bg-surface p-4 shadow-sm"
    >
      <h2 id={headingId} className="mb-3 text-base font-semibold">
        {cell.question}
      </h2>
      <CellBody {...props} />
    </article>
  )
}

function CellBody({ cell, busy, onAsk, onRetry, onRephrase }: CellProps) {
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
    case 'answer':
      return (
        <Suspense fallback={<RunningBody />}>
          <AnswerCell reply={reply} />
        </Suspense>
      )
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
      <p className="text-sm text-fg-muted">Working on it… this usually takes a few seconds.</p>
      <Skeleton className="h-4 w-3/4 motion-reduce:animate-none" />
      <Skeleton className="h-4 w-1/2 motion-reduce:animate-none" />
      <Skeleton className="h-24 w-full motion-reduce:animate-none" />
    </div>
  )
}
