import { useQuery } from '@tanstack/react-query'

import { api, call } from './client'
import type { components } from './schema'
import { fromTokens, refreshDone, setSession, type Session } from './session'

// The login routes (U7.1, PLAN 5.1). The session itself (token in memory, refresh, authFetch) is in
// session.ts, which the API client uses; this file uses the client, so the two don't import each other.

type Schemas = components['schemas']
export type LoginRequest = Schemas['LoginRequest']
export type SignupRequest = Schemas['SignupRequest']
export type WorkspaceChoice = Schemas['WorkspaceChoice']

const AUTH_TIMEOUT_MS = 15_000

/** POST /auth/login. Throws ApiError (e.g. invalid_login) on failure. */
export async function logIn(body: LoginRequest): Promise<Session> {
  const tokens = await call(AUTH_TIMEOUT_MS, undefined, (signal) =>
    api.POST('/auth/login', { body, signal }),
  )
  const next = fromTokens(tokens)
  setSession(next)
  return next
}

/** POST /auth/signup: a new account and its own workspace, logged in. Throws ApiError (email_taken…). */
export async function signUp(body: SignupRequest): Promise<Session> {
  const tokens = await call(AUTH_TIMEOUT_MS, undefined, (signal) =>
    api.POST('/auth/signup', { body, signal }),
  )
  const next = fromTokens(tokens)
  setSession(next)
  return next
}

/** POST /auth/logout, then forget the session here, even if the API couldn't be reached. */
export async function logOut(): Promise<void> {
  try {
    await call(AUTH_TIMEOUT_MS, undefined, (signal) => api.POST('/auth/logout', { signal }))
  } catch {
    // The cookie may outlive this (it expires on its own); this tab is logged out either way.
  } finally {
    setSession(null)
  }
}

// --- Workspaces (U7.1d, PLAN 5.12): every login can belong to several -----------------------------

/** The cache key of your workspaces. Cleared, like everything, when the workspace changes. */
export const WORKSPACES_QUERY = ['workspaces'] as const

export function getWorkspaces(signal?: AbortSignal): Promise<WorkspaceChoice[]> {
  return call(AUTH_TIMEOUT_MS, signal, (s) => api.GET('/auth/workspaces', { signal: s })).then(
    ({ workspaces }) => workspaces,
  )
}

/** Your workspaces, yours first, the current one marked. `enabled: false` while logged out. */
export function useWorkspaces({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: WORKSPACES_QUERY,
    queryFn: ({ signal }) => getWorkspaces(signal),
    enabled,
    staleTime: 5 * 60_000,
  })
}

/**
 * Move this login to another of your workspaces: a new access token, and the refresh cookie
 * replaced (a reload stays there). Waits for a refresh under way first: both replace the cookie,
 * and the server takes a reused old one for a stolen token. The session listeners then drop the
 * old workspace's data (see `onWorkspaceChange`). Throws ApiError (member_not_found…).
 */
export async function switchWorkspace(workspaceId: string): Promise<Session> {
  await refreshDone()
  const tokens = await call(AUTH_TIMEOUT_MS, undefined, (signal) =>
    api.POST('/auth/switch', { body: { workspace_id: workspaceId }, signal }),
  )
  const next = fromTokens(tokens)
  setSession(next, { chosen: true })
  return next
}
