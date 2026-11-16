/**
 * The Settings page's calls (U7.1b). The LLM key half: which provider and model answer this
 * workspace's questions, and its key, which goes in once and never comes back (only "…WXYZ").
 */
import { useQuery } from '@tanstack/react-query'

import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type ProviderOption = Schemas['ProviderOption']
export type ProviderId = ProviderOption['id']
export type LLMKeyInfo = Schemas['LLMKeyInfo']
export type SaveLLMKeyRequest = Schemas['SaveLLMKeyRequest']

const READ_TIMEOUT_MS = 10_000
// Saving makes one test call with the key: the server gives it 20 s and no retry
// (llm/key_check.py), so wait a little longer than that. Shorter, and the page would say "took too
// long" while the server saves the key anyway.
const SAVE_TIMEOUT_MS = 35_000

/** The cache key of the workspace's saved LLM (null: the demo key). Cleared with the workspace. */
export const LLM_KEY_QUERY = ['llm-key'] as const

export function getProviders(signal?: AbortSignal): Promise<ProviderOption[]> {
  return call(READ_TIMEOUT_MS, signal, (s) => api.GET('/llm/providers', { signal: s }))
}

/** The workspace's own LLM, or `null`: its questions run on the free demo key. */
export function getLLMKey(signal?: AbortSignal): Promise<LLMKeyInfo | null> {
  return call(READ_TIMEOUT_MS, signal, (s) => api.GET('/llm/key', { signal: s }))
}

/**
 * Save (or replace) it, after the server's test call with the key (admins). Not a react-query
 * mutation on purpose: a mutation keeps its variables (here the key, in plain text) in the cache
 * for minutes after it ends.
 */
export function saveLLMKey(body: SaveLLMKeyRequest): Promise<LLMKeyInfo> {
  return call(SAVE_TIMEOUT_MS, undefined, (s) => api.PUT('/llm/key', { body, signal: s }))
}

/** Remove it: questions go back to the demo key (admins). */
export function deleteLLMKey(): Promise<void> {
  return call(READ_TIMEOUT_MS, undefined, (s) => api.DELETE('/llm/key', { signal: s }))
}

// The list is code on the server (llm/catalog.py): it changes only with a new release.
const PROVIDERS_STALE_MS = 60 * 60_000

export function useProviders() {
  return useQuery({
    queryKey: ['llm-providers'],
    queryFn: ({ signal }) => getProviders(signal),
    staleTime: PROVIDERS_STALE_MS,
  })
}

export function useLLMKey() {
  return useQuery({ queryKey: LLM_KEY_QUERY, queryFn: ({ signal }) => getLLMKey(signal) })
}
