import { createContext, useCallback, useContext } from 'react'

import type { ConversationDetail, RerunResult } from '@/api/conversations'

import { conversationAt, type CanvasStore, type Conversation } from './state'

/** Navigation state: the question to put in the composer on arrival. */
export type DraftState = { draft: string }

export type CanvasContextValue = {
  store: CanvasStore
  ask: (key: string, question: string, parentId?: string) => void
  retry: (key: string, cellId: string) => void
  stop: (cellId: string) => void
  newConversation: () => void
  /** Starts over at an empty `/` and asks this there, as a new conversation. */
  askNew: (question: string) => void
  /** Starts over at an empty `/`, with this question in the composer, not sent. */
  draftNew: (question: string) => void
  /** Puts a saved conversation on the canvas, if nothing is there yet. */
  load: (conversation: ConversationDetail) => void
  /** Fresh rows for a saved cell. */
  applyRerun: (key: string, cellId: string, result: RerunResult) => void
  /** Removes a deleted conversation from this page's memory. */
  dropConversation: (conversationId: string) => void
}

/** Filled by CanvasProvider (AppShell), above the routes, so `/` → `/c/<id>` keeps the state. */
export const CanvasContext = createContext<CanvasContextValue | null>(null)

function useCanvas(): CanvasContextValue {
  const canvas = useContext(CanvasContext)
  if (canvas === null) throw new Error('This must be used inside <CanvasProvider>')
  return canvas
}

export type ConversationView = {
  conversation: Conversation
  /** A question is running: the composer and the examples wait (one question at a time). */
  busy: boolean
  ask: (question: string, parentId?: string) => void
  /** Runs a failed cell's question again, in that cell. */
  retry: (cellId: string) => void
  /** Stops a running cell's question. */
  stop: (cellId: string) => void
  load: (conversation: ConversationDetail) => void
  applyRerun: (cellId: string, result: RerunResult) => void
}

/** One conversation (`key` = NEW_CONVERSATION or a conversation id), and a way to ask in it. */
export function useConversation(key: string): ConversationView {
  const { store, ask: askIn, retry: retryIn, stop, load, applyRerun: rerunIn } = useCanvas()

  const conversation = conversationAt(store, key)
  const busy = conversation.cells.some((cell) => cell.status === 'running')
  const ask = useCallback(
    (question: string, parentId?: string) => askIn(key, question, parentId),
    [askIn, key],
  )
  const retry = useCallback((cellId: string) => retryIn(key, cellId), [retryIn, key])
  const applyRerun = useCallback(
    (cellId: string, result: RerunResult) => rerunIn(key, cellId, result),
    [rerunIn, key],
  )
  return { conversation, busy, ask, retry, stop, load, applyRerun }
}

/** What the history list does to the canvas. */
export function useHistoryActions() {
  const { newConversation, dropConversation, askNew } = useCanvas()
  return { newConversation, dropConversation, askNew }
}

/** Opens a new conversation with this question in the composer. Spends nothing until it's sent. */
export function useDraftNew(): (question: string) => void {
  return useCanvas().draftNew
}

/** Starts over at an empty `/`. The old conversation stays at its own URL. */
export function useNewConversation(): () => void {
  return useCanvas().newConversation
}
