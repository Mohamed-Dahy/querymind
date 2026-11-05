/** Saved conversations: list, read, rename, pin, delete, and re-run a turn for fresh rows. */
import { queryOptions } from '@tanstack/react-query'

import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type ConversationSummary = Schemas['ConversationSummary']
export type ConversationDetail = Schemas['ConversationDetail']
export type SavedTurn = Schemas['SavedTurnResponse']
export type ConversationList = Schemas['ConversationList']
export type ConversationUpdate = Schemas['ConversationUpdate']
export type RerunResult = Schemas['RerunResponse']

// What TanStack Query files the history list under, so a new answer can refresh it.
export const CONVERSATIONS_KEY = ['conversations'] as const

const TIMEOUT_MS = 10_000
const RERUN_TIMEOUT_MS = 30_000 // a query can take a while

/** How many conversations the history rail asks for at first (and per "Show more"). */
export const HISTORY_PAGE = 30

/** The history list as TanStack Query fetches it. Shared, so two places asking share one request. */
export function conversationListQuery(query: string, limit: number) {
  return queryOptions({
    queryKey: [...CONVERSATIONS_KEY, query, limit],
    queryFn: ({ signal }) => listConversations({ q: query, limit }, signal),
  })
}

export function listConversations(
  { q, limit }: { q?: string | undefined; limit: number },
  signal?: AbortSignal,
): Promise<ConversationList> {
  const query = q ? { q, limit } : { limit }
  return call(TIMEOUT_MS, signal, (s) =>
    api.GET('/conversations', { params: { query }, signal: s }),
  )
}

export function getConversation(id: string, signal?: AbortSignal): Promise<ConversationDetail> {
  return call(TIMEOUT_MS, signal, (s) =>
    api.GET('/conversations/{conversation_id}', {
      params: { path: { conversation_id: id } },
      signal: s,
    }),
  )
}

export function updateConversation(
  id: string,
  change: ConversationUpdate,
): Promise<ConversationSummary> {
  return call(TIMEOUT_MS, undefined, (s) =>
    api.PATCH('/conversations/{conversation_id}', {
      params: { path: { conversation_id: id } },
      body: change,
      signal: s,
    }),
  )
}

/** `keepalive`: the request still goes out if the page is closing. */
export async function deleteConversation(
  id: string,
  { keepalive = false }: { keepalive?: boolean } = {},
): Promise<void> {
  await call(TIMEOUT_MS, undefined, (s) =>
    api.DELETE('/conversations/{conversation_id}', {
      params: { path: { conversation_id: id } },
      signal: s,
      keepalive,
    }),
  )
}

/** Fresh rows from a saved answer's own SQL. Nothing is sent but which turn. */
export function rerunTurn(id: string, n: number): Promise<RerunResult> {
  return call(RERUN_TIMEOUT_MS, undefined, (s) =>
    api.POST('/conversations/{conversation_id}/turns/{n}/rerun', {
      params: { path: { conversation_id: id, n } },
      signal: s,
    }),
  )
}
