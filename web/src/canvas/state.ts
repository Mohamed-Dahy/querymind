/**
 * The canvas state: conversations made of cells, changed only by these pure reducers.
 *
 * A reducer is a function (state, action) → new state. It never changes the old state and never
 * calls the API, so every rule here is tested without React (state.test.ts). The provider
 * (CanvasProvider.tsx) does the side effects: it calls the API, then dispatches what happened.
 * U2's streaming events will become more actions on the same cells.
 */
import type { AskReply } from '@/api/client'
import type { ConversationDetail, RerunResult } from '@/api/conversations'
import type { TraceEvent } from '@/api/stream'

/** The key of the conversation that has no id yet: the canvas at `/`. */
export const NEW_CONVERSATION = 'new'

/** Why a question failed: the API's stable code and its message for people (client.ts ApiError). */
export type CellError = {
  code: string
  message: string
  status?: number
  /** A rate limit's wait, as the API said it. */
  retryAfterSeconds?: number
  /** When that wait ends (a timestamp, not a counter: leaving the page doesn't restart it). */
  retryAt?: number
}

export type CellStatus = 'running' | 'done' | 'failed'

/** One question and what came back for it. */
export type Cell = {
  id: string
  question: string
  /** The cell this one follows up on (U1.8). */
  parentId?: string
  status: CellStatus
  /** Set when status is 'done': an answer, a question back, or a refusal. */
  reply?: AskReply
  /** Set when status is 'failed'. */
  error?: CellError
  /** What the agent reported so far: kept after the end, to show how it got there. */
  trace?: TraceEvent[]
  /** Set on a cell that came from the saved history, whose rows aren't stored. */
  saved?: SavedInfo
}

export type SavedInfo = {
  /** Its turn number, which is all a re-run needs. */
  n: number
  /** When the answer was made. */
  createdAt: string
  /** When the rows now shown were read: after a re-run. They may not match the answer's words. */
  freshAt?: string
}

/** `id` is null until the server's first reply gives the conversation one. */
export type Conversation = { id: string | null; cells: Cell[] }

export type ConversationAction =
  | { type: 'asked'; cellId: string; question: string; parentId?: string }
  | { type: 'traced'; cellId: string; event: TraceEvent }
  | { type: 'replied'; cellId: string; reply: AskReply }
  | { type: 'failed'; cellId: string; error: CellError }
  | { type: 'retried'; cellId: string }
  | { type: 'rerun'; cellId: string; result: RerunResult }

/** One conversation's next state. An action for a cell that can't take it changes nothing. */
export function conversationReducer(state: Conversation, action: ConversationAction): Conversation {
  switch (action.type) {
    case 'asked': {
      const { cellId, question, parentId } = action
      const cell: Cell = { id: cellId, question, status: 'running' }
      if (parentId !== undefined) cell.parentId = parentId
      return { ...state, cells: [...state.cells, cell] }
    }
    case 'traced':
      return updateCell(state, action.cellId, 'running', (cell) => ({
        ...cell,
        trace: [...(cell.trace ?? []), action.event],
      }))
    case 'replied': {
      const next = updateCell(state, action.cellId, 'running', (cell) => ({
        ...withoutResult(cell),
        status: 'done',
        reply: action.reply,
      }))
      // Every reply kind carries the conversation's id; the first one names the conversation.
      if (next === state || state.id !== null) return next
      return { ...next, id: action.reply.conversation_id }
    }
    case 'failed':
      return updateCell(state, action.cellId, 'running', (cell) => ({
        ...withoutResult(cell),
        status: 'failed',
        error: action.error,
      }))
    case 'rerun':
      return updateCell(state, action.cellId, 'done', (cell) => {
        if (cell.saved === undefined || cell.reply?.kind !== 'answer') return cell
        const { columns, rows, row_count, truncated, ran_at: freshAt } = action.result
        return {
          ...cell,
          reply: { ...cell.reply, columns, rows, row_count, truncated },
          saved: { ...cell.saved, freshAt },
        }
      })
    case 'retried':
      // Same cell, same place on the canvas: it runs again instead of a new cell being added.
      return updateCell(state, action.cellId, 'failed', (cell) => {
        const { trace: _oldRun, ...rest } = withoutResult(cell)
        return { ...rest, status: 'running' }
      })
  }
}

/** Replace one cell, but only if it is in the expected status; otherwise return `state` itself. */
function updateCell(
  state: Conversation,
  cellId: string,
  expected: CellStatus,
  change: (cell: Cell) => Cell,
): Conversation {
  const index = state.cells.findIndex((cell) => cell.id === cellId && cell.status === expected)
  if (index === -1) return state
  const cells = state.cells.with(index, change(state.cells[index] as Cell))
  return { ...state, cells }
}

function withoutResult({ reply: _reply, error: _error, ...cell }: Cell): Cell {
  return cell
}

/**
 * Every conversation on this page load, by key: NEW_CONVERSATION, or the server's id.
 * Kept in memory only (saved history arrives in U3).
 */
export type CanvasStore = {
  conversations: Readonly<Record<string, Conversation>>
  /**
   * The id the new conversation got from its first reply. The URL changes from `/` to `/c/<id>`
   * a moment after the state does; until a reset, `/` shows that conversation, so the canvas
   * never flashes empty in between.
   */
  promotedTo: string | null
}

export type CanvasAction =
  | (ConversationAction & { key: string })
  | { type: 'reset' }
  | { type: 'loaded'; conversation: ConversationDetail }
  | { type: 'dropped'; conversationId: string }

export const EMPTY_STORE: CanvasStore = { conversations: {}, promotedTo: null }

/** The key the conversation is really stored under (`new` may have been promoted). */
function resolveKey(store: CanvasStore, key: string): string {
  return key === NEW_CONVERSATION && store.promotedTo !== null ? store.promotedTo : key
}

/** The conversation at `key`, or an empty one: `/` has no id yet, `/c/<id>` already has its id. */
export function conversationAt(store: CanvasStore, key: string): Conversation {
  const resolved = resolveKey(store, key)
  return (
    store.conversations[resolved] ?? {
      id: resolved === NEW_CONVERSATION ? null : resolved,
      cells: [],
    }
  )
}

/** A saved conversation as cells. Their rows aren't stored: a re-run brings fresh ones. */
export function cellsFromSaved({ id, saved_turns: turns }: ConversationDetail): Cell[] {
  return turns.map(({ n, created_at: createdAt, reply, trace }) => ({
    id: `saved-${id}-${n}`,
    question: reply.question,
    status: 'done',
    reply,
    // An empty trace means it didn't stream: no rail, as after a fallback.
    ...(trace.length > 0 && { trace }),
    saved: { n, createdAt },
  }))
}

/** The whole store's next state: one conversation changes, and the new one may get its id. */
export function canvasReducer(store: CanvasStore, action: CanvasAction): CanvasStore {
  if (action.type === 'reset') {
    // A fresh `/`. The promoted conversation stays under its id (its URL still works).
    const { [NEW_CONVERSATION]: _new, ...conversations } = store.conversations
    return { conversations, promotedTo: null }
  }
  if (action.type === 'loaded') {
    const { id } = action.conversation
    // Never over live cells: a question asked while it was loading must not vanish.
    if (conversationAt(store, id).cells.length > 0) return store
    const loaded: Conversation = { id, cells: cellsFromSaved(action.conversation) }
    return { ...store, conversations: { ...store.conversations, [id]: loaded } }
  }
  if (action.type === 'dropped') {
    const { [action.conversationId]: _gone, ...conversations } = store.conversations
    const promotedTo = store.promotedTo === action.conversationId ? null : store.promotedTo
    return { conversations, promotedTo }
  }

  const key = resolveKey(store, action.key)
  const current = conversationAt(store, key)
  const next = conversationReducer(current, action)
  if (next === current) return store

  // The new conversation just got its id: file it under that id, and remember it for `/`.
  if (key === NEW_CONVERSATION && next.id !== null) {
    const { [NEW_CONVERSATION]: _new, ...rest } = store.conversations
    return { conversations: { ...rest, [next.id]: next }, promotedTo: next.id }
  }
  return { ...store, conversations: { ...store.conversations, [key]: next } }
}
