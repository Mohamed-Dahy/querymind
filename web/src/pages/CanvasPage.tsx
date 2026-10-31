import { useEffect, useRef } from 'react'
import { useParams } from 'react-router'

import { errorTitle } from '@/api/errors'
import { Cell } from '@/canvas/Cell'
import { Composer } from '@/canvas/Composer'
import { EmptyCanvas } from '@/canvas/EmptyCanvas'
import { NEW_CONVERSATION, type Cell as CellData } from '@/canvas/state'
import { useConversation } from '@/canvas/useConversation'

/** What a screen reader hears about the latest cell (the cells themselves aren't live regions). */
function announcement(cell: CellData | undefined): string {
  if (cell === undefined) return ''
  if (cell.status === 'running') return 'Working on your question.'
  if (cell.status === 'failed') return errorTitle(cell.error?.code ?? 'unexpected')
  switch (cell.reply?.kind) {
    case 'clarification':
      return 'QueryMind has a question for you.'
    case 'refusal':
      return 'QueryMind declined this question.'
    default:
      return 'Answer ready.'
  }
}

/** The canvas: example questions or the conversation's cells, and the composer at the bottom. */
export function CanvasPage() {
  // `/` is the new conversation; `/c/<id>` is the conversation with that id.
  const { conversationId } = useParams()
  const { conversation, busy, ask } = useConversation(conversationId ?? NEW_CONVERSATION)
  const { cells } = conversation

  // Bring a new cell into view (the composer stays at the bottom of the screen).
  const lastCell = useRef<HTMLLIElement>(null)
  useEffect(() => {
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    lastCell.current?.scrollIntoView?.({
      block: 'nearest',
      behavior: reduceMotion ? 'auto' : 'smooth',
    })
  }, [cells.length])

  return (
    <div className="mx-auto flex min-h-full max-w-5xl flex-col px-4 pt-10">
      <h1 className="text-2xl">Ask about your data</h1>
      <p className="mt-2 text-fg-muted">
        Questions about the Northwind shop and the Chinook music store, in plain language. Every
        answer shows the SQL that produced it.
      </p>

      <div className="mt-8 flex-1">
        {cells.length === 0 ? (
          <EmptyCanvas onAsk={ask} busy={busy} />
        ) : (
          <ol aria-label="Questions and answers" className="space-y-4">
            {cells.map((cell, index) => (
              <li key={cell.id} ref={index === cells.length - 1 ? lastCell : undefined}>
                <Cell cell={cell} />
              </li>
            ))}
          </ol>
        )}
      </div>

      {/* Stays at the bottom of the screen while the cells above it scroll. */}
      <div className="sticky bottom-0 mt-8 bg-linear-to-t from-bg from-70% pt-4 pb-4">
        <p aria-live="polite" className="sr-only">
          {announcement(cells.at(-1))}
        </p>
        <Composer onSubmit={ask} busy={busy} />
      </div>
    </div>
  )
}
