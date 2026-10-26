import { describe, expect, it, vi } from 'vitest'

import { ApiError, ask, getHealth, type AskResponse } from './client'
import { errorTitle } from './errors'
import { shouldRetry } from './queryClient'

/** Answer every fetch with `impl` for one test (vite.config.ts restores the real fetch after it). */
function stubFetch(impl: (request: Request) => Promise<Response>) {
  const fetchMock = vi.fn<(request: Request) => Promise<Response>>(impl)
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

const ANSWER: AskResponse = {
  kind: 'answer',
  question: 'How many customers are there?',
  conversation_id: '7f0c5a52-3c1e-4c55-9a59-0d4c3c1f6a01',
  answer: 'There are 91 customers.',
  answer_from_llm: true,
  sql: 'SELECT count(*) FROM customers LIMIT 1000',
  explanation: 'Counts the rows in customers.',
  columns: ['count'],
  rows: [[91]],
  row_count: 1,
  truncated: false,
  warnings: [],
  confidence: 'high',
  confidence_reasons: [],
}

/** What fetch rejects with when a signal aborts: the signal's reason. */
async function rejectWithReason(request: Request): Promise<Response> {
  await new Promise((resolve) => request.signal.addEventListener('abort', resolve))
  throw request.signal.reason
}

describe('getHealth', () => {
  it('calls /api/health on the same origin and returns the typed body', async () => {
    const fetchMock = stubFetch(async () => Response.json({ status: 'ok', version: '0.1.0' }))

    await expect(getHealth()).resolves.toEqual({ status: 'ok', version: '0.1.0' })

    const request = fetchMock.mock.calls[0]?.[0]
    expect(request?.method).toBe('GET')
    expect(request?.url).toBe(`${window.location.origin}/api/health`)
  })
})

describe('ask', () => {
  it('posts the question as JSON and returns the reply', async () => {
    const fetchMock = stubFetch(async () => Response.json(ANSWER))

    await expect(ask({ question: 'How many customers are there?' })).resolves.toEqual(ANSWER)

    const request = fetchMock.mock.calls[0]?.[0]
    expect(request?.method).toBe('POST')
    expect(request?.url).toBe(`${window.location.origin}/api/ask`)
    expect(await request?.json()).toEqual({ question: 'How many customers are there?' })
  })

  it('turns the API error body into an ApiError with code, message and status', async () => {
    stubFetch(async () =>
      Response.json(
        { error: 'unsafe_sql', message: 'Only SELECT queries are allowed.' },
        { status: 422 },
      ),
    )

    const error = await ask({ question: 'Delete everything' }).catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({
      code: 'unsafe_sql',
      message: 'Only SELECT queries are allowed.',
      status: 422,
    })
  })

  it('rejects a reply with an unknown kind', async () => {
    stubFetch(async () => Response.json({ ...ANSWER, kind: 'poem' }))

    await expect(ask({ question: 'q' })).rejects.toMatchObject({ code: 'bad_response' })
  })
})

describe('failures without an API error body', () => {
  it('a gateway error (the dev proxy with uvicorn stopped) means the API is unreachable', async () => {
    stubFetch(async () => new Response(null, { status: 502 }))

    await expect(getHealth()).rejects.toMatchObject({ code: 'api_unreachable', status: 502 })
  })

  it('any other status without our body is an unexpected response', async () => {
    stubFetch(async () => new Response('<html>oops</html>', { status: 500 }))

    await expect(getHealth()).rejects.toMatchObject({
      code: 'bad_response',
      message: 'The API answered with HTTP 500.',
    })
  })

  it('a network failure means the API is unreachable', async () => {
    stubFetch(async () => {
      throw new TypeError('Failed to fetch')
    })

    await expect(getHealth()).rejects.toMatchObject({ code: 'api_unreachable' })
  })

  it('a success body that is not JSON is an unexpected response', async () => {
    stubFetch(async () => new Response('not json', { status: 200 }))

    await expect(getHealth()).rejects.toMatchObject({ code: 'bad_response' })
  })

  it('a request that runs out of time is a timeout', async () => {
    vi.useFakeTimers()
    try {
      stubFetch(rejectWithReason)
      const result = getHealth().catch((e: unknown) => e)
      await vi.advanceTimersByTimeAsync(10_000)

      expect(await result).toMatchObject({ code: 'api_timeout' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('a cancel by the caller is passed on as an AbortError, not turned into an API error', async () => {
    stubFetch(rejectWithReason)
    const controller = new AbortController()

    const result = getHealth(controller.signal).catch((e: unknown) => e)
    controller.abort()

    const error = await result
    expect(error).not.toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ name: 'AbortError' })
  })
})

describe('errorTitle', () => {
  it('has a title for every code, and a generic one for unknown codes', () => {
    expect(errorTitle('unsafe_sql')).toBe("I couldn't answer that safely.")
    expect(errorTitle('api_unreachable')).toBe("The QueryMind API isn't reachable.")
    expect(errorTitle('something_new')).toBe('Something went wrong.')
  })
})

describe('shouldRetry', () => {
  it('retries a transient failure once, and nothing else', () => {
    const unreachable = new ApiError('api_unreachable', 'down')
    expect(shouldRetry(0, unreachable)).toBe(true)
    expect(shouldRetry(1, unreachable)).toBe(false)
    expect(shouldRetry(0, new ApiError('unsafe_sql', 'no'))).toBe(false)
    expect(shouldRetry(0, new Error('bug'))).toBe(false)
  })
})
