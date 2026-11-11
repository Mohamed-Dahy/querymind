import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getHealth } from './client'
import {
  authFetch,
  getSession,
  refreshSession,
  resetAuth,
  safeNext,
  setSession,
  takeSessionEnded,
  type TokenResponse,
} from './session'

import { AUTH_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { mockSession, mockUser } from '@/mocks/session'

const ORIGIN = window.location.origin

function tokens(token: string): TokenResponse {
  return { access_token: token, token_type: 'bearer', expires_in: 900, user: mockUser }
}

/** Every Authorization header the fake API saw on `path`. */
function recordAuth(path: string, reply: () => Response = () => HttpResponse.json({})) {
  const seen: (string | null)[] = []
  server.use(
    http.all(path, ({ request }) => {
      seen.push(request.headers.get('Authorization'))
      return reply()
    }),
  )
  return seen
}

describe('authFetch', () => {
  it('sends the access token with every request', async () => {
    const seen = recordAuth('/api/health', () => HttpResponse.json({ status: 'ok', version: '1' }))

    await getHealth()

    expect(seen).toEqual(['Bearer mock-access-token'])
  })

  it('on a 401, refreshes once and sends the same request again, body and all', async () => {
    const bodies: string[] = []
    const headers: (string | null)[] = []
    server.use(
      http.post(AUTH_URL.refresh, () => HttpResponse.json(tokens('renewed-token'))),
      http.post('/api/ask', async ({ request }) => {
        bodies.push(await request.text())
        headers.push(request.headers.get('Authorization'))
        return headers.length === 1
          ? HttpResponse.json(
              { error: 'not_authenticated', message: 'Please log in.' },
              { status: 401 },
            )
          : HttpResponse.json({ ok: true })
      }),
    )

    const response = await authFetch(
      new Request(`${ORIGIN}/api/ask`, { method: 'POST', body: '{"question":"q"}' }),
    )

    expect(response.status).toBe(200)
    // The POST body is sent twice: the retry is a copy made before the first send.
    expect(bodies).toEqual(['{"question":"q"}', '{"question":"q"}'])
    expect(headers).toEqual(['Bearer mock-access-token', 'Bearer renewed-token'])
    expect(getSession()?.accessToken).toBe('renewed-token')
  })

  it('when the refresh is refused too, gives back the 401 and logs out', async () => {
    server.use(
      http.post(AUTH_URL.refresh, () =>
        HttpResponse.json(
          { error: 'not_authenticated', message: 'Please log in.' },
          { status: 401 },
        ),
      ),
      http.get('/api/conversations', () =>
        HttpResponse.json(
          { error: 'not_authenticated', message: 'Please log in.' },
          { status: 401 },
        ),
      ),
    )

    const response = await authFetch(new Request(`${ORIGIN}/api/conversations`))

    expect(response.status).toBe(401)
    expect(getSession()).toBeNull()
    expect(takeSessionEnded()).toBe(true) // the login page says the session ended
    expect(takeSessionEnded()).toBe(false) // once
  })

  it('never refreshes for a 401 from the login routes themselves', async () => {
    let refreshes = 0
    server.use(
      http.post(AUTH_URL.refresh, () => {
        refreshes += 1
        return HttpResponse.json(tokens('x'))
      }),
    )

    const response = await authFetch(
      new Request(`${ORIGIN}/api/auth/login`, {
        method: 'POST',
        body: JSON.stringify({ email: 'a@b.co', password: 'wrong password' }),
      }),
    )

    expect(response.status).toBe(401) // invalid_login: a wrong password, not an old token
    expect(refreshes).toBe(0)
  })

  it('a Stop still reaches the request after a garbage collection', async () => {
    // Seen in CI (Node 22.23): stream.test.ts's Stop test hung. Node links a copied Request's signal
    // to the original's only weakly, so once the original was collected, a Stop never arrived.
    const gc = await exposeGc()
    let sent: Request | undefined
    vi.stubGlobal('fetch', async (request: Request) => {
      sent = request
      return new Response('{}')
    })
    const caller = new AbortController()

    // The original is made here and not kept: only authFetch's copy is still around afterwards.
    await authFetch(
      new Request(`${ORIGIN}/api/ask`, {
        method: 'POST',
        body: '{}',
        signal: AbortSignal.any([caller.signal]),
      }),
    )
    for (let round = 0; round < 5; round++) {
      gc()
      await new Promise((resolve) => setTimeout(resolve, 10))
    }
    caller.abort()

    expect(sent?.signal.aborted).toBe(true)
  })
})

/** Node's garbage collector, callable from a test (V8 flag set at run time, read in a fresh context). */
async function exposeGc(): Promise<() => void> {
  // Specifiers in variables: the app's types don't include Node's, and these run only under Vitest.
  const v8Module = 'node:v8'
  const vmModule = 'node:vm'
  const v8 = (await import(/* @vite-ignore */ v8Module)) as {
    setFlagsFromString: (flags: string) => void
  }
  const vm = (await import(/* @vite-ignore */ vmModule)) as {
    runInNewContext: (code: string) => unknown
  }
  v8.setFlagsFromString('--expose-gc')
  return vm.runInNewContext('gc') as () => void
}

describe('refreshSession', () => {
  beforeEach(() => resetAuth())

  it('shares one refresh among everyone asking at once', async () => {
    let refreshes = 0
    server.use(
      http.post(AUTH_URL.refresh, () => {
        refreshes += 1
        return HttpResponse.json(tokens('shared'))
      }),
    )

    const sessions = await Promise.all([refreshSession(), refreshSession(), refreshSession()])

    // Each refresh replaces the cookie: a second one with the old cookie would look stolen.
    expect(refreshes).toBe(1)
    expect(sessions.map((s) => s?.accessToken)).toEqual(['shared', 'shared', 'shared'])
  })

  it('with the API unreachable on page load, settles on logged out', async () => {
    server.use(http.post(AUTH_URL.refresh, () => HttpResponse.error()))

    expect(await refreshSession()).toBeNull()
    expect(getSession()).toBeNull()
    expect(takeSessionEnded()).toBe(false) // nothing ended: nobody was logged in
  })

  it('keeps a session when the API is only unreachable for a moment', async () => {
    setSession(mockSession)
    server.use(http.post(AUTH_URL.refresh, () => HttpResponse.error()))

    expect(await refreshSession()).toBeNull()
    expect(getSession()).toEqual(mockSession)
  })
})

describe('safeNext', () => {
  it.each([
    ['/c/123?x=1', '/c/123?x=1'],
    ['/evals/models', '/evals/models'],
    [null, '/'],
    ['', '/'],
    ['https://evil.example', '/'],
    ['//evil.example', '/'],
    ['/\\evil.example', '/'],
    ['javascript:alert(1)', '/'],
    ['/login?next=/x', '/'],
    ['/signup', '/'],
  ])('%s → %s', (next, expected) => {
    expect(safeNext(next)).toBe(expected)
  })
})
