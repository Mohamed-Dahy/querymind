import { useEffect, useSyncExternalStore } from 'react'

import type { components } from './schema'

// The login session (U7.1, PLAN 5.1, ADR 011). The login calls themselves are in auth.ts.
//
// The access token lives in this module's memory only: not in localStorage, where any script that got
// into the page could read it later. A reload forgets it, so the app asks /auth/refresh on load: the
// browser sends the httpOnly refresh cookie (JavaScript can't read it) and a new access token comes back.
// Every API call goes through `authFetch`, which adds "Authorization: Bearer …" and, on a 401, refreshes
// once and tries again.

type Schemas = components['schemas']
export type Me = Schemas['Me']
export type TokenResponse = Schemas['TokenResponse']

export type Session = { accessToken: string; user: Me }
/** checking = the first refresh hasn't answered yet; nothing is known. */
export type AuthStatus = 'checking' | 'in' | 'out'

const AUTH_PREFIX = '/api/auth/'

let session: Session | null = null
let checked = false // has a refresh (or a login) answered since the page loaded?
let ended = false // did a session end on its own (refresh refused), not by "Log out"?
let refreshing: Promise<Session | null> | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function statusNow(): AuthStatus {
  if (session) return 'in'
  return checked ? 'out' : 'checking'
}

export function getSession(): Session | null {
  return session
}

/** Sets who is logged in (or nobody). Tests seed one with this, before rendering. */
export function setSession(next: Session | null) {
  session = next
  checked = true
  if (next) ended = false
  emit()
}

/** Back to "nothing known", as on a fresh page load (tests use it between tests). */
export function resetAuth() {
  session = null
  checked = false
  ended = false
  refreshing = null
  emit()
}

/** True once, after a session ended without "Log out" (the login page says so). */
export function takeSessionEnded(): boolean {
  const was = ended
  ended = false
  return was
}

export function fromTokens(tokens: TokenResponse): Session {
  return { accessToken: tokens.access_token, user: tokens.user }
}

/**
 * A new access token from the refresh cookie. Everyone who asks at the same moment shares one request
 * (StrictMode mounts twice; several queries can get a 401 together), because each refresh replaces the
 * cookie, and a second one with the old cookie would look like a stolen token to the server.
 *
 * Resolves to the new session, or null when the server says no (the session is then cleared). If the
 * API can't be reached, nothing changes and it resolves to null.
 */
export function refreshSession(): Promise<Session | null> {
  refreshing ??= (async () => {
    try {
      const response = await globalThis.fetch(
        new Request(`${window.location.origin}${AUTH_PREFIX}refresh`, { method: 'POST' }),
      )
      if (!response.ok) {
        if (session) ended = true
        setSession(null)
        return null
      }
      const next = fromTokens((await response.json()) as TokenResponse)
      setSession(next)
      return next
    } catch {
      // Unreachable API: logged in or not is unknown. A first check settles on "out" (the login page,
      // which can then say the API is down); a later one keeps the current session.
      if (!checked) setSession(null)
      return null
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

/**
 * Each copy's original, kept alive as long as the copy is. A copy's signal follows the original's
 * (so Stop still works), but Node links the two only weakly: once the original was garbage-collected,
 * a Stop never reached the copy and a stream hung (seen in CI, Node 22.23). The WeakMap holds the
 * original only while the copy exists, so nothing outlives the request.
 */
const originals = new WeakMap<Request, Request>()

/** A fresh copy of the request with the current access token. */
function withToken(request: Request, body: ArrayBuffer | undefined): Request {
  const copy = new Request(request, body === undefined ? {} : { body }) // keeps its signal (Stop)
  originals.set(copy, request)
  if (session) copy.headers.set('Authorization', `Bearer ${session.accessToken}`)
  return copy
}

/**
 * fetch with the access token. A 401 from any route but /auth/* gets one refresh and one retry.
 *
 * A request's body can be read only once, so it's read into memory first and each send gets its own
 * copy. (Not `request.clone()`: a cloned body is a stream, and browsers then send it as a streamed
 * upload, which some tools in between, Playwright among them, can't see.)
 */
export async function authFetch(request: Request): Promise<Response> {
  const isAuthRoute = new URL(request.url).pathname.startsWith(AUTH_PREFIX)
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD'
  const body = hasBody ? await request.arrayBuffer() : undefined
  const response = await globalThis.fetch(withToken(request, body))
  if (response.status !== 401 || isAuthRoute) return response
  const renewed = await refreshSession()
  if (renewed === null) return response // logged out now: the caller shows its 401
  return globalThis.fetch(withToken(request, body))
}

/** Who is logged in (null: nobody), redrawn when it changes. */
export function useSession(): Session | null {
  return useSyncExternalStore(subscribe, getSession, getSession)
}

/** in / out / checking. The first use on a page load starts the refresh that settles it. */
export function useAuthStatus(): AuthStatus {
  const status = useSyncExternalStore(subscribe, statusNow, statusNow)
  useEffect(() => {
    if (status === 'checking') void refreshSession()
  }, [status])
  return status
}

/**
 * Where to go after logging in: `?next=` if it's a path inside this app. Anything else (another site,
 * `//evil.example`, `javascript:`) goes home: never an open redirect.
 */
export function safeNext(next: string | null): string {
  if (!next || !next.startsWith('/') || next.startsWith('//') || next.startsWith('/\\')) return '/'
  if (next.startsWith('/login') || next.startsWith('/signup')) return '/'
  return next
}
