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
// The auth routes that never refresh on a 401: they are how a session starts or ends. The others
// (/auth/me, /auth/workspaces, /auth/switch) need the access token like any route (U7.1d).
const NO_REFRESH = ['login', 'signup', 'refresh', 'logout'].map((name) => AUTH_PREFIX + name)

let session: Session | null = null
let checked = false // has a refresh (or a login) answered since the page loaded?
let ended = false // did a session end on its own (refresh refused), not by "Log out"?
let refreshing: Promise<Session | null> | null = null
let checkingMe: Promise<void> | null = null
const listeners = new Set<() => void>()

/** How the session moved to another workspace: the user chose it, or a refresh landed there. */
export type WorkspaceChange = { from: Me; to: Me; chosen: boolean }
const workspaceListeners = new Set<(change: WorkspaceChange) => void>()

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

/**
 * Sets who is logged in (or nobody). Tests seed one with this, before rendering.
 *
 * Moving from one workspace to another (U7.1d) first tells `onWorkspaceChange`'s listeners, before
 * anything re-renders: the old workspace's cached data must be gone before the new one is drawn.
 * `chosen`: the user switched (the account menu); otherwise a refresh landed elsewhere, e.g. after
 * being removed from the workspace.
 */
export function setSession(next: Session | null, { chosen = false }: { chosen?: boolean } = {}) {
  const before = session?.user
  session = next
  checked = true
  if (next) ended = false
  if (before && next && before.workspace.id !== next.user.workspace.id) {
    for (const listener of workspaceListeners) listener({ from: before, to: next.user, chosen })
  }
  emit()
}

/**
 * Called when the session moves to another workspace, synchronously, before the app re-renders.
 * Returns the unsubscribe function (for a useEffect).
 */
export function onWorkspaceChange(listener: (change: WorkspaceChange) => void): () => void {
  workspaceListeners.add(listener)
  return () => {
    workspaceListeners.delete(listener)
  }
}

/** Who is logged in, as the API says now (/auth/me): same token, maybe a new role or name. */
export function updateUser(user: Me) {
  if (!session || JSON.stringify(session.user) === JSON.stringify(user)) return
  setSession({ ...session, user })
}

/** Wait for a refresh that is under way, if any: a switch must not race it (both replace the cookie). */
export async function refreshDone(): Promise<void> {
  await refreshing
}

/** Back to "nothing known", as on a fresh page load (tests use it between tests). */
export function resetAuth() {
  session = null
  checked = false
  ended = false
  refreshing = null
  checkingMe = null
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
  // A request that names its own token keeps it (U7.1d): a delete confirmed in one workspace is
  // sent with that workspace's token, even if the session has moved on since.
  if (session && !request.headers.has('Authorization')) {
    copy.headers.set('Authorization', `Bearer ${session.accessToken}`)
  }
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
  const path = new URL(request.url).pathname
  const startsOrEnds = NO_REFRESH.includes(path)
  const hasBody = request.method !== 'GET' && request.method !== 'HEAD'
  const body = hasBody ? await request.arrayBuffer() : undefined
  const workspace = session?.user.workspace.id
  const response = await globalThis.fetch(withToken(request, body))
  // Refused: your role may have changed (U7.1d). Ask who you are now, without waiting for it.
  if (response.status === 403 && !path.startsWith(AUTH_PREFIX)) void checkMe()
  if (response.status !== 401 || startsOrEnds) return response
  const renewed = await refreshSession()
  if (renewed === null) return response // logged out now: the caller shows its 401
  // The refresh landed in another workspace (removed from this one): never replay the request
  // there. A question meant for one workspace must not run, and be saved, in another.
  if (renewed.user.workspace.id !== workspace) return response
  return globalThis.fetch(withToken(request, body))
}

/**
 * Ask the API who is logged in now and update the session (role, workspace name): after a 403, and
 * when the tab comes back into focus (U7.1d). One request at a time; a failure changes nothing.
 */
export function checkMe(): Promise<void> {
  checkingMe ??= (async () => {
    try {
      if (!session) return
      const response = await authFetch(
        new Request(`${window.location.origin}${AUTH_PREFIX}me`, { method: 'GET' }),
      )
      if (response.ok) updateUser((await response.json()) as Me)
    } catch {
      // Unreachable: keep what we have.
    } finally {
      checkingMe = null
    }
  })()
  return checkingMe
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
