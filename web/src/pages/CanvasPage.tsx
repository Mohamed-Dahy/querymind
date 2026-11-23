import { CornerDownRight, Info } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'

import { ApiError } from '@/api/client'
import { chooseDatabase } from '@/api/database'
import { getConversation } from '@/api/conversations'
import { errorTitle } from '@/api/errors'
import { Cell } from '@/canvas/Cell'
import { Composer, type ComposerHandle } from '@/canvas/Composer'
import { ConsoleStatus } from '@/canvas/ConsoleStatus'
import { ConversationDatabase } from '@/canvas/conversationDatabase'
import { EmptyCanvas } from '@/canvas/EmptyCanvas'
import { FirstRunHint } from '@/canvas/FirstRunHint'
import { NEW_CONVERSATION, type Cell as CellData } from '@/canvas/state'
import { railFrom, repairAnnouncement } from '@/canvas/trace'
import { useConversation, type DraftState } from '@/canvas/useConversation'
import { formatWait } from '@/canvas/wait'
import { Button } from '@/components/ui/button'
import { DatabaseSwitcher } from '@/layout/DatabaseSwitcher'
import { DEMO_NAME, explorerPath, useDatabase } from '@/layout/useDatabase'
import { KEEP_FOCUS } from '@/layout/useFocusOnRouteChange'
import { ChannelBar, CropMarks, Plate } from '@/components/Instrument'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

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
    const title = errorTitle(cell.error?.code ?? 'unexpected', cell.error?.limit)
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

  // Opening a conversation about another database switches the app to it (decided with
  // Mohamed): its follow-ups go there anyway, so what the switcher says matches what is asked.
  // Once per conversation, when its database is known: switching afterwards starts a new one.
  const database = useDatabase()
  // The ref is reset on the empty canvas: this component can stay mounted from `/c/A` to `/` and
  // back, and Back to A must switch to A's database again.
  const synced = useRef<string | null>(null)
  const { id: openId, connectionId } = conversation
  useEffect(() => {
    if (openId === null) {
      synced.current = null
      return
    }
    if (connectionId === undefined || synced.current === openId) return
    synced.current = openId
    chooseDatabase(connectionId)
  }, [openId, connectionId])

  // A question sent here from the explorer. Used once: the state is cleared, so a reload or coming
  // back doesn't put it in the composer again. On the next frame, because the shell moves focus to
  // the page after this effect has run, and the box should have it.
  const { state } = useLocation()
  const navigate = useNavigate()
  const draft = (state as DraftState | null)?.draft
  useEffect(() => {
    if (draft === undefined) return
    composer.current?.fill(draft)
    requestAnimationFrame(() => composer.current?.focus())
    void navigate('/', { replace: true, state: KEEP_FOCUS })
  }, [draft, navigate])

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

  // The home console (U9.3, ADR 027): a new conversation with nothing asked yet. Not a saved one
  // that is still loading or failed to load: that keeps the canvas layout, so the box doesn't jump
  // from the middle to the bottom bar when its cells arrive.
  const home = conversationId === undefined && cells.length === 0
  const ownDatabase = database.id === null ? null : database.name

  return (
    <div
      className={cn('mx-auto flex min-h-full max-w-5xl flex-col px-4', home ? 'py-12' : 'pt-10')}
    >
      {home ? (
        <ConsoleHeader ownDatabase={ownDatabase} />
      ) : (
        <>
          <p aria-hidden="true" className="label-mono">
            CANVAS · LIVE AGENT
          </p>
          <h1 className="mt-2 text-3xl">Ask about your data</h1>
          <div className="mt-3">
            <DatabaseSwitcher place="page" />
          </div>
          <p className="mt-2 text-fg-muted">{intro(ownDatabase)}</p>
        </>
      )}

      {/* Before the question box: on the home console, the way into your own database (its words
          say "below"); on the canvas, the conversation's cells. */}
      {home ? (
        database.id !== null && (
          <div className="mt-8">
            <OwnDatabaseStart name={database.name} id={database.id} />
          </div>
        )
      ) : (
        <div className="mt-8 flex-1">
          {cells.length === 0 && saved.isFetching ? (
            <div aria-hidden="true" className="space-y-3">
              <Skeleton className="h-24 w-full motion-reduce:animate-none" />
              <Skeleton className="h-24 w-full motion-reduce:animate-none" />
            </div>
          ) : cells.length === 0 ? (
            <>
              {saved.error && <NotLoadedNotice error={saved.error} />}
              <FirstRunHint />
              {database.id === null ? (
                <EmptyCanvas onAsk={ask} busy={busy} />
              ) : (
                <OwnDatabaseStart name={database.name} id={database.id} />
              )}
            </>
          ) : (
            // scroll-mb: when the browser scrolls a cell or a focused control into view, it stops
            // above the sticky composer (about 200 px tall on a phone) instead of behind it.
            <ConversationDatabase value={connectionId ?? database.id}>
              <ol aria-label="Questions and answers" className="space-y-4 [&_*]:scroll-mb-56">
                {cells.map((cell, index) => (
                  <li key={cell.id} ref={index === cells.length - 1 ? lastCell : undefined}>
                    <Cell
                      cell={cell}
                      index={index}
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
            </ConversationDatabase>
          )}
        </div>
      )}

      {/* The question box. On the home console: the centrepiece, in a panel with a channel bar and
          the status strip. On the canvas: it stays at the bottom of the screen while the cells
          scroll. These two divs and the Composer are the same elements in both layouts (only their
          classes change), so React never remounts the box: the text and the keyboard focus stay in
          it when the first question turns the console into the canvas. */}
      <div
        className={
          home ? 'relative mt-10' : 'sticky bottom-0 mt-8 bg-linear-to-t from-bg from-70% pt-4 pb-4'
        }
      >
        <p aria-live="polite" className="sr-only">
          {announcement(cells.at(-1))}
        </p>
        {followingUp && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-signal/40 bg-surface px-3 py-1.5 text-sm">
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
        {home && <CropMarks />}
        <div
          className={cn(
            home &&
              'overflow-hidden rounded-cell border bg-surface shadow-lg transition-[box-shadow,border-color] duration-300 focus-within:glow focus-within:border-transparent',
          )}
        >
          {home && (
            <div aria-hidden="true">
              <ChannelBar live={!busy} label="CH-1 · CONSOLE">
                <span className="label-mono">{busy ? 'ANSWERING' : 'READY'}</span>
              </ChannelBar>
            </div>
          )}
          {home && <ConsoleStatus source={ownDatabase ?? DEMO_NAME} />}
          <Composer
            ref={composer}
            framed={!home}
            describedBy={followingUp && followUpNoteId}
            onSubmit={(question) => {
              ask(question, followingUp?.id)
              setFollowingUpId(null)
            }}
            busy={busy}
          />
        </div>
      </div>

      {/* After the box, in reading and Tab order too: the first-visit hint and the examples. */}
      {home && (
        <div className="mt-12">
          <FirstRunHint />
          {database.id === null && <EmptyCanvas onAsk={ask} busy={busy} />}
        </div>
      )}
    </div>
  )
}

/** What the page says it is about, under the heading. */
function intro(ownDatabase: string | null): string {
  const about =
    ownDatabase === null
      ? 'Questions about the Northwind shop and the Chinook music store, in plain language.'
      : `Questions about ${ownDatabase}, your own database, in plain language.`
  return `${about} Every answer shows the SQL that produced it.`
}

/**
 * The top of the home console (U9.3): the model plate, the heading at instrument scale, and what it
 * is about. Left-aligned on the 12-column grid, with the text kept to a readable width.
 */
function ConsoleHeader({ ownDatabase }: { ownDatabase: string | null }) {
  return (
    <div className="grid gap-y-5 md:grid-cols-12">
      <div aria-hidden="true" className="md:col-span-12">
        <Plate code="QM-1">CANVAS · LIVE AGENT</Plate>
      </div>
      <h1 className="text-5xl leading-[0.95] font-semibold tracking-[-0.035em] sm:text-6xl md:col-span-10">
        Ask about your data
      </h1>
      <div className="space-y-3 md:col-span-7">
        <DatabaseSwitcher place="page" />
        <p className="text-fg-muted">{intro(ownDatabase)}</p>
      </div>
    </div>
  )
}

/** `/c/<id>` that couldn't be loaded: gone, or history is off. Asking there still works. */
function NotLoadedNotice({ error }: { error: Error }) {
  const gone = error instanceof ApiError && error.code === 'conversation_not_found'
  return (
    <p className="mb-4 flex items-start gap-2 rounded-cell border bg-surface px-3 py-2 text-sm">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      {gone
        ? "This conversation wasn't found. It may have been deleted, or it was never saved. A question here starts it again."
        : "This conversation's earlier questions can't be shown right now. You can still ask here: the server may remember them for a while."}
    </p>
  )
}

/**
 * The start of a conversation about the workspace's own database: no example questions (they are
 * the demo's), but a way to see what it holds first.
 */
function OwnDatabaseStart({ name, id }: { name: string; id: string }) {
  return (
    <div className="rounded-cell border bg-surface p-5 text-sm">
      <p aria-hidden="true" className="label-mono mb-1.5">
        YOUR DATABASE
      </p>
      <p className="font-medium">Ask anything about {name}</p>
      <p className="mt-1 text-fg-muted">
        Not sure what&rsquo;s in it?{' '}
        <Link to={explorerPath('/explore', id)} className="text-info underline underline-offset-4">
          Look at its tables in the explorer
        </Link>
        , then ask in your own words below.
      </p>
    </div>
  )
}
