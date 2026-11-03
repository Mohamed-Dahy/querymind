import { CornerDownRight, Info } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { useParams } from 'react-router'

import { ApiError } from '@/api/client'
import { getConversation } from '@/api/conversations'
import { errorTitle } from '@/api/errors'
import { Cell } from '@/canvas/Cell'
import { Composer, type ComposerHandle } from '@/canvas/Composer'
import { EmptyCanvas } from '@/canvas/EmptyCanvas'
import { NEW_CONVERSATION, type Cell as CellData } from '@/canvas/state'
import { railFrom, repairAnnouncement } from '@/canvas/trace'
import { useConversation } from '@/canvas/useConversation'
import { formatWait } from '@/canvas/wait'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'

import { NotFoundPage } from './NotFoundPage'

/** What a screen reader hears about the latest cell (the cells themselves aren't live regions). */
function announcement(cell: CellData | undefined): string {
  // Opening a saved conversation isn't news: nothing just finished.
  if (cell === undefined || cell.saved !== undefined) return ''
  if (cell.status === 'running') {
    const repairing = repairAnnouncement(railFrom(cell.trace ?? [], { ended: false }))
    return repairing || 'Working on your question.'
  }
  if (cell.status === 'failed') {
    const title = errorTitle(cell.error?.code ?? 'unexpected')
    const wait = cell.error?.retryAfterSeconds
    return wait === undefined ? title : `${title} You can try again in ${formatWait(wait)}.`
  }
  switch (cell.reply?.kind) {
    case 'clarification':
      return 'QueryMind has a question for you.'
    case 'refusal':
      return 'QueryMind declined this question.'
    default:
      return 'Answer ready.'
  }
}

// The API only takes a UUID as a conversation id, so anything else can't be a conversation.
const CONVERSATION_ID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** `/` is the new conversation; `/c/<id>` is the conversation with that id. */
export function CanvasPage() {
  const { conversationId } = useParams()
  if (conversationId !== undefined && !CONVERSATION_ID.test(conversationId)) {
    return <NotFoundPage />
  }
  return <Canvas conversationId={conversationId} />
}

/** The canvas: example questions or the conversation's cells, and the composer at the bottom. */
function Canvas({ conversationId }: { conversationId: string | undefined }) {
  const { conversation, busy, ask, retry, stop, load, applyRerun } = useConversation(
    conversationId ?? NEW_CONVERSATION,
  )
  const { cells } = conversation
  const composer = useRef<ComposerHandle>(null)
  const followUpNoteId = useId()

  // After a reload, or a shared link: nothing in memory, so fetch what was saved.
  const saved = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: ({ signal }) => getConversation(conversationId as string, signal),
    enabled: conversationId !== undefined && cells.length === 0,
    retry: false,
    gcTime: 0, // once it's on the canvas this copy isn't needed; after a delete it would be wrong
  })
  useEffect(() => {
    if (saved.data) load(saved.data)
  }, [saved.data, load])

  // Looked up in this conversation's cells, so it ends by itself when the conversation changes.
  const [followingUpId, setFollowingUpId] = useState<string | null>(null)
  const followingUp = cells.find((cell) => cell.id === followingUpId)

  // Bring the latest cell into view when it is added, and again when its reply makes it taller.
  const lastCell = useRef<HTMLLIElement>(null)
  const lastStatus = cells.at(-1)?.status
  useEffect(() => {
    const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    lastCell.current?.scrollIntoView?.({
      block: 'nearest',
      behavior: reduceMotion ? 'auto' : 'smooth',
    })
  }, [cells.length, lastStatus])

  return (
    <div className="mx-auto flex min-h-full max-w-5xl flex-col px-4 pt-10">
      <h1 className="text-2xl">Ask about your data</h1>
      <p className="mt-2 text-fg-muted">
        Questions about the Northwind shop and the Chinook music store, in plain language. Every
        answer shows the SQL that produced it.
      </p>

      <div className="mt-8 flex-1">
        {cells.length === 0 && saved.isFetching ? (
          <div aria-hidden="true" className="space-y-3">
            <Skeleton className="h-24 w-full motion-reduce:animate-none" />
            <Skeleton className="h-24 w-full motion-reduce:animate-none" />
          </div>
        ) : cells.length === 0 ? (
          <>
            {saved.error && <NotLoadedNotice error={saved.error} />}
            <EmptyCanvas onAsk={ask} busy={busy} />
          </>
        ) : (
          // scroll-mb: when the browser scrolls a cell or a focused control into view, it stops
          // above the sticky composer (about 200 px tall on a phone) instead of behind it.
          <ol aria-label="Questions and answers" className="space-y-4 [&_*]:scroll-mb-56">
            {cells.map((cell, index) => (
              <li key={cell.id} ref={index === cells.length - 1 ? lastCell : undefined}>
                <Cell
                  cell={cell}
                  first={index === 0}
                  parentQuestion={cells.find(({ id }) => id === cell.parentId)?.question}
                  busy={busy}
                  onAsk={ask}
                  onRetry={(cellId) => {
                    retry(cellId)
                    // The Retry button is about to go away: don't lose the keyboard focus with it.
                    composer.current?.focus()
                  }}
                  onRephrase={(question) => composer.current?.fill(question)}
                  onStop={(cellId) => {
                    stop(cellId)
                    // The Stop button goes away too.
                    composer.current?.focus()
                  }}
                  onRerun={applyRerun}
                  onFollowUp={(cellId) => {
                    setFollowingUpId(cellId)
                    composer.current?.focus()
                  }}
                />
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
        {followingUp && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border bg-surface px-3 py-1.5 text-sm">
            <CornerDownRight aria-hidden="true" className="size-4 shrink-0 text-signal" />
            <p id={followUpNoteId} className="min-w-0 flex-1 truncate">
              Following up on: {followingUp.question}
            </p>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setFollowingUpId(null)
                composer.current?.focus()
              }}
            >
              Cancel follow-up
            </Button>
          </div>
        )}
        <Composer
          ref={composer}
          describedBy={followingUp && followUpNoteId}
          onSubmit={(question) => {
            ask(question, followingUp?.id)
            setFollowingUpId(null)
          }}
          busy={busy}
        />
      </div>
    </div>
  )
}

/** `/c/<id>` that couldn't be loaded: gone, or history is off. Asking there still works. */
function NotLoadedNotice({ error }: { error: Error }) {
  const gone = error instanceof ApiError && error.code === 'conversation_not_found'
  return (
    <p className="mb-4 flex items-start gap-2 rounded-lg border bg-surface px-3 py-2 text-sm">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      {gone
        ? "This conversation wasn't found. It may have been deleted, or it was never saved. A question here starts it again."
        : "This conversation's earlier questions can't be shown right now. You can still ask here: the server may remember them for a while."}
    </p>
  )
}
