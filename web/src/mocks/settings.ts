/**
 * The fake API's saved LLM key (U7.1b). In `localStorage`, like the conversations, so a reload in
 * mock mode keeps it. Only what the real API keeps: provider, model, the last 4 characters. A few
 * keys show each error the form must handle (as WRONG_PASSWORD does for the login page).
 */
import type {
  ConnectionInfo,
  LLMKeyInfo,
  ProviderOption,
  SaveConnectionRequest,
  SaveLLMKeyRequest,
} from '@/api/settings'

import type { Catalog, SemanticOverview } from '@/api/explorer'

import { catalog, providers } from './fixtures'

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

// --- Databases (U7.1b part 2) ---------------------------------------------------------------------

const CONNECTIONS_KEY = 'querymind-mock-connections'

/**
 * What to type in the add form to see each refusal; anything else is saved. Each answers with the
 * real API's status and message (errors.json, checked against the API by test_web_fixtures.py).
 */
export const CONNECTION_TRIGGERS = {
  /** host → `connection_unsafe` (a private address) */
  unsafeHost: 'private.example.com',
  /** host → `connection_unreachable` */
  unreachableHost: 'unreachable.example.com',
  /** host → `connection_tls_failed` */
  selfSignedHost: 'selfsigned.example.com',
  /** host → `rate_limited`: too many checks, wait 30 s */
  tooManyTriesHost: 'too-many-tries.example.com',
  /** user → `connection_writable`, with the REVOKE that fixes it */
  writerUser: 'writer',
  /** password → `connection_login_failed` */
  wrongPassword: 'wrong password',
} as const

export function loadMockConnections(): ConnectionInfo[] {
  try {
    return JSON.parse(localStorage.getItem(CONNECTIONS_KEY) ?? '[]') as ConnectionInfo[]
  } catch {
    return []
  }
}

function saveAll(connections: ConnectionInfo[]) {
  localStorage.setItem(CONNECTIONS_KEY, JSON.stringify(connections))
}

/** Saved as the real API would (no password kept); null when the name is taken. */
export function saveMockConnection(request: SaveConnectionRequest): ConnectionInfo | null {
  const all = loadMockConnections()
  if (all.some(({ name }) => name === request.name)) return null
  const now = new Date().toISOString()
  const info: ConnectionInfo = {
    id: crypto.randomUUID(),
    name: request.name,
    host: request.host,
    port: request.port,
    dbname: request.dbname,
    user: request.user,
    sslmode: request.sslmode,
    checked_at: now,
    created_at: now,
  }
  saveAll([...all, info])
  return info
}

export function testMockConnection(id: string): ConnectionInfo | null {
  const all = loadMockConnections()
  const found = all.find((connection) => connection.id === id)
  if (!found) return null
  const checked = { ...found, checked_at: new Date().toISOString() }
  saveAll(all.map((connection) => (connection.id === id ? checked : connection)))
  return checked
}

export function deleteMockConnection(id: string): boolean {
  const all = loadMockConnections()
  saveAll(all.filter((connection) => connection.id !== id))
  return all.some((connection) => connection.id === id)
}

/** Is this one of the fake workspace's saved databases? */
export function isMockConnection(id: string): boolean {
  return findMockConnection(id) !== undefined
}

export function findMockConnection(id: string): ConnectionInfo | undefined {
  return loadMockConnections().find((connection) => connection.id === id)
}

/**
 * A saved database in the explorer (U7.1b): the demo's Northwind tables. Before onboarding they
 * have no business; after it (U7.1c), the described ones belong to the database's own, named
 * after it, as the real API's layer from the dictionary does.
 */
export function savedDatabaseCatalog(
  described: { name: string; tables: Record<string, unknown> } | null = null,
): Catalog {
  return {
    tables: catalog.tables
      .filter(({ business }) => business === 'northwind')
      .map((table) => ({
        ...table,
        business: described && table.name in described.tables ? described.name : null,
      })),
  }
}

export const NOT_ONBOARDED: SemanticOverview = { businesses: [] }
