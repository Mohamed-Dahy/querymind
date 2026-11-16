/**
 * Onboarding a saved database and reviewing its data dictionary (U7.1c). Onboarding reads the
 * database's tables and drafts a description of each with the workspace's own LLM key, in the
 * background; an admin then corrects the drafts, flags personal data and marks tables reviewed.
 */
import { useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'

import { ApiError, api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type OnboardingInfo = Schemas['OnboardingInfo']
export type OnboardingState = OnboardingInfo['status']
export type DataDictionary = Schemas['DataDictionary']
export type DictionaryTable = Schemas['DictionaryTable']
export type TableEdit = Schemas['TableEdit']
export type DictionaryEdit = Schemas['DictionaryEdit']

const READ_TIMEOUT_MS = 10_000
// Starting is behind the same "too many checks" limit as testing a database, and answers at once
// (202): the run itself goes on in the background.
const START_TIMEOUT_MS = 15_000

/** While a run is going on, how often its progress is asked for. One small read on the server. */
export const ONBOARDING_POLL_MS = 1500

const RUNNING: readonly OnboardingState[] = ['queued', 'reading', 'drafting']

/** Still going: poll, and refuse edits (the run's result would overwrite them). */
export function isRunning(status: OnboardingState | undefined): boolean {
  return status !== undefined && RUNNING.includes(status)
}

export const onboardingKey = (id: string) => ['onboarding', id] as const
export const dictionaryKey = (id: string) => ['dictionary', id] as const

/**
 * Where the database's onboarding is, or `null`: it has never been onboarded (the API's 404).
 * Admins only: the counts are the whole database's.
 */
export async function getOnboarding(
  id: string,
  signal?: AbortSignal,
): Promise<OnboardingInfo | null> {
  try {
    return await call(READ_TIMEOUT_MS, signal, (s) =>
      api.GET('/connections/{connection_id}/onboarding', {
        params: { path: { connection_id: id } },
        signal: s,
      }),
    )
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === 'dictionary_not_found') return null
    throw caught
  }
}

/** Start a run (admins). Tables already described or reviewed are kept: a new run fills gaps. */
export function startOnboarding(id: string): Promise<OnboardingInfo> {
  return call(START_TIMEOUT_MS, undefined, (s) =>
    api.POST('/connections/{connection_id}/onboard', {
      params: { path: { connection_id: id } },
      signal: s,
    }),
  )
}

/** The drafted descriptions. A restricted role gets only the tables it may use. */
export function getDictionary(id: string, signal?: AbortSignal): Promise<DataDictionary> {
  return call(READ_TIMEOUT_MS, signal, (s) =>
    api.GET('/connections/{connection_id}/dictionary', {
      params: { path: { connection_id: id } },
      signal: s,
    }),
  )
}

/**
 * Save one table's review (admins). The API replaces the table's description, "reviewed" mark and
 * (when sent) personal-data flags with what is sent, so `edit` must be the whole table, not only
 * what changed: see `tableEdit` in settings/dictionaryDraft.ts. The other tables keep theirs.
 */
export function saveDictionaryTable(
  id: string,
  table: string,
  edit: TableEdit,
): Promise<DataDictionary> {
  return call(READ_TIMEOUT_MS, undefined, (s) =>
    api.PUT('/connections/{connection_id}/dictionary', {
      params: { path: { connection_id: id } },
      body: { tables: { [table]: edit } },
      signal: s,
    }),
  )
}

/**
 * Progress, asked again every ONBOARDING_POLL_MS while a run is going on. When a poll finds the
 * run over, the database's descriptions are refreshed, here and not in a page: whichever page is
 * polling (the Databases tab, or a review page that went read-only for the run) gets the new
 * dictionary. A review page still showing the old one could save an old, empty description back
 * over a fresh draft.
 */
export function useOnboarding(id: string, { enabled = true }: { enabled?: boolean } = {}) {
  const queryClient = useQueryClient()
  return useQuery({
    queryKey: onboardingKey(id),
    queryFn: async ({ signal }) => {
      const before = queryClient.getQueryData<OnboardingInfo | null>(onboardingKey(id))
      const now = await getOnboarding(id, signal)
      if (isRunning(before?.status) && !isRunning(now?.status)) {
        refreshDescriptions(queryClient, id)
      }
      return now
    },
    enabled,
    refetchInterval: (query) => (isRunning(query.state.data?.status) ? ONBOARDING_POLL_MS : false),
  })
}

export function useDictionary(id: string) {
  return useQuery({
    queryKey: dictionaryKey(id),
    queryFn: ({ signal }) => getDictionary(id, signal),
  })
}

/**
 * After a run finished or a review was saved: what the explorer shows of this database (its
 * meanings, its personal-data flags) and its dictionary are out of date.
 */
export function refreshDescriptions(queryClient: QueryClient, id: string) {
  for (const key of [['catalog', id], ['semantic', id], dictionaryKey(id)]) {
    void queryClient.invalidateQueries({ queryKey: key })
  }
}
