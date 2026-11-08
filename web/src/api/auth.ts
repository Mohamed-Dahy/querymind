import { api, call } from './client'
import type { components } from './schema'
import { fromTokens, setSession, type Session } from './session'

// The login routes (U7.1, PLAN 5.1). The session itself (token in memory, refresh, authFetch) is in
// session.ts, which the API client uses; this file uses the client, so the two don't import each other.

type Schemas = components['schemas']
export type LoginRequest = Schemas['LoginRequest']
export type SignupRequest = Schemas['SignupRequest']

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
