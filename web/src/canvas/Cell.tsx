import { CircleAlert } from 'lucide-react'
import { lazy, Suspense } from 'react'

import { errorTitle } from '@/api/errors'
import { Skeleton } from '@/components/ui/skeleton'

import { ClarificationCell } from './ClarificationCell'
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
}

/**
 * One question on the canvas, and whatever came back for it. The frame (the question as a heading)
 * is shared; the body depends on the status and the reply's `kind`.
 */
export function Cell({ cell, busy, onAsk }: CellProps) {
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
      <CellBody cell={cell} busy={busy} onAsk={onAsk} />
    </article>
  )
}

function CellBody({ cell, busy, onAsk }: CellProps) {
  if (cell.status === 'running') return <RunningBody />
  if (cell.status === 'failed' || cell.reply === undefined) {
    // U1.7 turns this into the error cell (Retry, a countdown for rate limits).
    const error = cell.error ?? { code: 'unexpected', message: '' }
    return (
      <div className="flex items-start gap-2 text-sm">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
        <div>
          <p className="font-medium">{errorTitle(error.code)}</p>
          {error.message && <p className="text-fg-muted">{error.message}</p>}
        </div>
      </div>
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
