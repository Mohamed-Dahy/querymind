/**
 * The canvas state: conversations made of cells, changed only by these pure reducers.
 *
 * A reducer is a function (state, action) → new state. It never changes the old state and never
 * calls the API, so every rule here is tested without React (state.test.ts). The provider
 * (CanvasProvider.tsx) does the side effects: it calls the API, then dispatches what happened.
 * U2's streaming events will become more actions on the same cells.
 */
import type { AskReply } from '@/api/client'

/** The key of the conversation that has no id yet: the canvas at `/`. */
export const NEW_CONVERSATION = 'new'

/** Why a question failed: the API's stable code and its message for people (client.ts ApiError). */
export type CellError = { code: string; message: string; status?: number }

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
}

/** `id` is null until the server's first reply gives the conversation one. */
export type Conversation = { id: string | null; cells: Cell[] }

export type ConversationAction =
  | { type: 'asked'; cellId: string; question: string; parentId?: string }
  | { type: 'replied'; cellId: string; reply: AskReply }
  | { type: 'failed'; cellId: string; error: CellError }
  | { type: 'retried'; cellId: string }

/** One conversation's next state. An action for a cell that can't take it changes nothing. */
export function conversationReducer(state: Conversation, action: ConversationAction): Conversation {
  switch (action.type) {
    case 'asked': {
      const { cellId, question, parentId } = action
      const cell: Cell = { id: cellId, question, status: 'running' }
      if (parentId !== undefined) cell.parentId = parentId
      return { ...state, cells: [...state.cells, cell] }
    }
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
    case 'retried':
      // Same cell, same place on the canvas: it runs again instead of a new cell being added.
      return updateCell(state, action.cellId, 'failed', (cell) => ({
        ...withoutResult(cell),
        status: 'running',
      }))
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

export type CanvasAction = (ConversationAction & { key: string }) | { type: 'reset' }

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

/** The whole store's next state: one conversation changes, and the new one may get its id. */
export function canvasReducer(store: CanvasStore, action: CanvasAction): CanvasStore {
  if (action.type === 'reset') {
    // A fresh `/`. The promoted conversation stays under its id (its URL still works).
    const { [NEW_CONVERSATION]: _new, ...conversations } = store.conversations
    return { conversations, promotedTo: null }
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
