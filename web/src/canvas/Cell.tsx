import { CircleAlert, MessageCircleQuestion, ShieldBan } from 'lucide-react'
import { lazy, Suspense } from 'react'

import { errorTitle } from '@/api/errors'
import { Skeleton } from '@/components/ui/skeleton'

import type { Cell as CellData } from './state'

// Loaded with the first answer, not with the page: the tabs and tooltip it uses (Radix) then stay
// out of the first download, which only needs the examples and the composer.
const AnswerCell = lazy(() =>
  import('./AnswerCell').then(({ AnswerCell }) => ({ default: AnswerCell })),
)

/**
 * One question on the canvas, and whatever came back for it. The frame (the question as a heading)
 * is shared; the body depends on the status and the reply's `kind`.
 */
export function Cell({ cell }: { cell: CellData }) {
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
      <CellBody cell={cell} />
    </article>
  )
}

function CellBody({ cell }: { cell: CellData }) {
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
      // U1.6 makes the options buttons that ask again in the same conversation.
      return (
        <div className="flex items-start gap-2 text-sm">
          <MessageCircleQuestion aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
          <div>
            <p className="font-medium">{reply.clarifying_question}</p>
            <ul className="mt-1 ml-4 list-disc text-fg-muted">
              {reply.options.map((option) => (
                <li key={option}>{option}</li>
              ))}
            </ul>
          </div>
        </div>
      )
    case 'refusal':
      // U1.6 gives refusals their own calm cell.
      return (
        <div className="flex items-start gap-2 text-sm">
          <ShieldBan aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
          <div>
            <p>{reply.message}</p>
            <p className="text-fg-muted">No SQL ran.</p>
          </div>
        </div>
      )
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
