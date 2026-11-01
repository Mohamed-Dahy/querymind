import { createContext, useCallback, useContext } from 'react'

import { conversationAt, type CanvasStore, type Conversation } from './state'

export type CanvasContextValue = {
  store: CanvasStore
  ask: (key: string, question: string, parentId?: string) => void
  retry: (key: string, cellId: string) => void
}

/** Filled by CanvasProvider (AppShell), above the routes, so `/` → `/c/<id>` keeps the state. */
export const CanvasContext = createContext<CanvasContextValue | null>(null)

export type ConversationView = {
  conversation: Conversation
  /** A question is running: the composer and the examples wait (one question at a time). */
  busy: boolean
  ask: (question: string, parentId?: string) => void
  /** Runs a failed cell's question again, in that cell. */
  retry: (cellId: string) => void
}

/** One conversation (`key` = NEW_CONVERSATION or a conversation id), and a way to ask in it. */
export function useConversation(key: string): ConversationView {
  const canvas = useContext(CanvasContext)
  if (canvas === null) throw new Error('useConversation must be used inside <CanvasProvider>')
  const { store, ask: askIn, retry: retryIn } = canvas

  const conversation = conversationAt(store, key)
  const busy = conversation.cells.some((cell) => cell.status === 'running')
  const ask = useCallback(
    (question: string, parentId?: string) => askIn(key, question, parentId),
    [askIn, key],
  )
  const retry = useCallback((cellId: string) => retryIn(key, cellId), [retryIn, key])
  return { conversation, busy, ask, retry }
}
