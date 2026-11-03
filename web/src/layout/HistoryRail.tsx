import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Pin, PinOff, Search, Trash2 } from 'lucide-react'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { Link, useLocation } from 'react-router'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import {
  CONVERSATIONS_KEY,
  listConversations,
  updateConversation,
  type ConversationSummary,
  type ConversationUpdate,
} from '@/api/conversations'
import { errorTitle } from '@/api/errors'
import { useHistoryActions } from '@/canvas/useConversation'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

import { groupConversations } from './history'
import type { DeleteWithUndo } from './useDeleteWithUndo'

const PAGE = 30
const SEARCH_DELAY_MS = 250

type HistoryRailProps = {
  deletes: DeleteWithUndo
  /** A conversation was opened: lets the phone's sheet close itself. */
  onOpen?: () => void
}

/** Saved conversations: pinned first, then by day, with search, rename, pin and delete. */
export function HistoryRail({ deletes, onOpen }: HistoryRailProps) {
  const [search, setSearch] = useState('')
  const [query, setQuery] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const searchBox = useRef<HTMLInputElement>(null)
  const labelId = useId()

  // Waits for a pause in the typing, not one request per letter.
  useEffect(() => {
    const timer = setTimeout(() => setQuery(search.trim()), SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [search])

  const conversations = useQuery({
    queryKey: [...CONVERSATIONS_KEY, query, limit],
    queryFn: ({ signal }) => listConversations({ q: query, limit }, signal),
    placeholderData: (previous) => previous, // the list stays while a new search loads
  })
  const visible = (conversations.data?.conversations ?? []).filter(
    ({ id }) => !deletes.hidden.has(id),
  )

  return (
    <div>
      <label htmlFor={labelId} className="sr-only">
        Search conversations
      </label>
      <div className="relative mt-2">
        <Search
          aria-hidden="true"
          className="absolute top-2 left-2 size-4 text-fg-muted pointer-coarse:top-3.5"
        />
        <input
          ref={searchBox}
          id={labelId}
          type="search"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search"
          className="h-8 w-full rounded-lg border border-input bg-muted pr-2 pl-7 text-sm outline-none placeholder:text-fg-muted focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:h-11"
        />
      </div>

      {conversations.isPending ? (
        <div aria-hidden="true" className="mt-3 space-y-2">
          <Skeleton className="h-4 w-full motion-reduce:animate-none" />
          <Skeleton className="h-4 w-2/3 motion-reduce:animate-none" />
        </div>
      ) : conversations.isError ? (
        <p className="mt-3 text-sm text-fg-muted">
          {conversations.error instanceof ApiError &&
          conversations.error.code === 'history_unavailable'
            ? "History isn't available on this server. Questions still work."
            : "History couldn't be loaded."}
        </p>
      ) : visible.length === 0 ? (
        <p className="mt-3 text-sm text-fg-muted">
          {query ? 'No conversation matches.' : 'Your past conversations will be listed here.'}
        </p>
      ) : (
        <>
          {groupConversations(visible, new Date(conversations.dataUpdatedAt)).map(
            ({ title, items }) => (
              <section key={title} className="mt-3">
                <h3 className="px-2 text-xs text-fg-muted">{title}</h3>
                <ul className="mt-1 space-y-0.5">
                  {items.map((conversation) => (
                    <HistoryItem
                      key={conversation.id}
                      conversation={conversation}
                      deletes={deletes}
                      onOpen={onOpen}
                      afterDelete={() => searchBox.current?.focus()}
                    />
                  ))}
                </ul>
              </section>
            ),
          )}
          {conversations.data?.has_more && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="mt-2 w-full"
              onClick={() => setLimit((current) => current + PAGE)}
            >
              Show more
            </Button>
          )}
        </>
      )}
    </div>
  )
}

const ACTIONS =
  // Out of the way until wanted, but still reachable: width 0 keeps them in the Tab order, so
  // focus reveals them. Touch screens have no hover, so there they always show.
  'flex w-0 shrink-0 overflow-hidden opacity-0 group-focus-within:w-auto group-focus-within:opacity-100 group-hover:w-auto group-hover:opacity-100 pointer-coarse:w-auto pointer-coarse:opacity-100'

function HistoryItem({
  conversation,
  deletes,
  onOpen,
  afterDelete,
}: {
  conversation: ConversationSummary
  deletes: DeleteWithUndo
  onOpen: (() => void) | undefined
  afterDelete: () => void
}) {
  const { id, title, pinned } = conversation
  const { pathname } = useLocation()
  const queryClient = useQueryClient()
  const { newConversation } = useHistoryActions()
  const [renaming, setRenaming] = useState(false)
  const renameButton = useRef<HTMLButtonElement>(null)
  const path = `/c/${id}`
  const current = pathname === path

  const update = useMutation({
    mutationFn: (change: ConversationUpdate) => updateConversation(id, change),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY }),
    onError: (error) => {
      toast.error(errorTitle(error instanceof ApiError ? error.code : 'unexpected'))
    },
  })

  function stopRenaming() {
    setRenaming(false)
    // The box is going away with the focus in it. After the button is back: next frame.
    requestAnimationFrame(() => renameButton.current?.focus())
  }

  if (renaming) {
    return (
      <li>
        <RenameBox
          title={title}
          onSave={(next) => {
            if (next !== title) update.mutate({ title: next })
            stopRenaming()
          }}
          onCancel={stopRenaming}
        />
      </li>
    )
  }

  return (
    <li className={cn('group flex items-center rounded-lg hover:bg-muted', current && 'bg-muted')}>
      <Link
        to={path}
        title={title}
        aria-current={current ? 'page' : undefined}
        onClick={onOpen}
        className={cn(
          'flex min-h-8 min-w-0 flex-1 items-center gap-1.5 rounded-lg px-2 text-sm pointer-coarse:min-h-11',
          current ? 'font-medium' : 'text-fg-muted',
        )}
      >
        {pinned && <Pin aria-label="Pinned" className="size-3 shrink-0" />}
        <span className="truncate">{title}</span>
      </Link>
      <div className={ACTIONS}>
        <IconButton
          ref={renameButton}
          label={`Rename “${title}”`}
          onClick={() => setRenaming(true)}
        >
          <Pencil aria-hidden="true" />
        </IconButton>
        <IconButton
          label={`${pinned ? 'Unpin' : 'Pin'} “${title}”`}
          onClick={() => update.mutate({ pinned: !pinned })}
        >
          {pinned ? <PinOff aria-hidden="true" /> : <Pin aria-hidden="true" />}
        </IconButton>
        <IconButton
          label={`Delete “${title}”`}
          onClick={() => {
            deletes.remove(id, title)
            if (current) newConversation() // don't stay on a page that is going away
            afterDelete()
          }}
        >
          <Trash2 aria-hidden="true" />
        </IconButton>
      </div>
    </li>
  )
}

function IconButton({
  label,
  onClick,
  ref,
  children,
}: {
  label: string
  onClick: () => void
  ref?: React.Ref<HTMLButtonElement>
  children: React.ReactNode
}) {
  return (
    <Button
      ref={ref}
      type="button"
      variant="ghost"
      size="icon-sm"
      aria-label={label}
      title={label}
      className="pointer-coarse:size-11"
      onClick={onClick}
    >
      {children}
    </Button>
  )
}

/** Enter saves, Escape or leaving the box cancels. An empty title isn't a title. */
function RenameBox({
  title,
  onSave,
  onCancel,
}: {
  title: string
  onSave: (title: string) => void
  onCancel: () => void
}) {
  const [text, setText] = useState(title)
  const box = useRef<HTMLInputElement>(null)

  useEffect(() => {
    box.current?.focus()
    box.current?.select()
  }, [])

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') {
      onCancel()
    } else if (event.key === 'Enter') {
      event.preventDefault()
      const next = text.trim()
      if (next) onSave(next)
    }
  }

  return (
    <input
      ref={box}
      aria-label="New title"
      value={text}
      maxLength={200}
      onChange={(event) => setText(event.target.value)}
      onKeyDown={onKeyDown}
      onBlur={onCancel}
      className="h-8 w-full rounded-lg border border-input bg-muted px-2 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:h-11"
    />
  )
}
