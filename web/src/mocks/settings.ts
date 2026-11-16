/**
 * The fake API's saved LLM key (U7.1b). In `localStorage`, like the conversations, so a reload in
 * mock mode keeps it. Only what the real API keeps: provider, model, the last 4 characters. A few
 * keys show each error the form must handle (as WRONG_PASSWORD does for the login page).
 */
import type { LLMKeyInfo, ProviderOption, SaveLLMKeyRequest } from '@/api/settings'

import { providers } from './fixtures'

const KEY = 'querymind-mock-llm-key'

/** Save this key to see the provider refuse it (`llm_key_invalid`, 422). */
export const INVALID_KEY = 'gsk_refused_by_provider'
/** Save this key to see "Too many keys tried" (`rate_limited`, 429, wait 30 s). */
export const TOO_MANY_TRIES_KEY = 'gsk_too_many_tries'

export function loadMockKey(): LLMKeyInfo | null {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? 'null') as LLMKeyInfo | null
  } catch {
    return null
  }
}

function provider(id: ProviderOption['id']): ProviderOption {
  // The request's provider was checked against the same list by the type; the real API answers 422.
  return providers.find((option) => option.id === id) ?? providers[0]!
}

export function saveMockKey(request: SaveLLMKeyRequest): LLMKeyInfo {
  const info: LLMKeyInfo = {
    provider: request.provider,
    provider_label: provider(request.provider).label,
    model: request.model,
    key_hint: request.api_key.slice(-4),
    updated_at: new Date().toISOString(),
  }
  localStorage.setItem(KEY, JSON.stringify(info))
  return info
}

export function deleteMockKey() {
  localStorage.removeItem(KEY)
}

/** The real API's wording for a refused key (llm/key_check.py), with this provider's name. */
export function refusedMessage(id: ProviderOption['id']): string {
  return `${provider(id).label} refused this key. Check it was copied whole and is still active.`
}
