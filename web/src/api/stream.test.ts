import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { conversationReducer, type Conversation } from '@/canvas/state'
import { answer, repair, repairStream } from '@/mocks/fixtures'
import { ASK_URL, STREAM_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { collectGarbage } from '@/test/gc'

import { ApiError } from './client'
import {
  askStream,
  createSseParser,
  STREAM_IDLE_TIMEOUT_MS,
  type StreamEvent,
  type TraceEvent,
} from './stream'

const encoder = new TextEncoder()

function sse(events: readonly StreamEvent[]): string {
  return events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join('')
}

/** A real response whose body arrives in exactly these pieces. */
function chunked(chunks: readonly Uint8Array[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(chunk)
      controller.close()
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

function split(bytes: Uint8Array, size: number): Uint8Array[] {
  const chunks: Uint8Array[] = []
  for (let start = 0; start < bytes.length; start += size) {
    chunks.push(bytes.slice(start, start + size))
  }
  return chunks
}

/** POST /ask/stream answers with these pieces; POST /ask counts its calls. */
function serve(response: () => Response) {
  const calls = { plain: 0 }
  server.use(
    http.post(STREAM_URL, response),
    http.post(ASK_URL, () => {
      calls.plain += 1
      return HttpResponse.json(answer)
    }),
  )
  return calls
}

/** A body that stays open, sends what `feed` gets, and fails when the request is aborted. */
// The requests the fake server is still answering. A real network layer holds an open request until
// it ends; without this, nothing held one here, and a garbage collection in mid-test took the request,
// its Stop wiring and the waiting reader with it: Stop went nowhere (CI only, 2026-11-11).
const serving = new Set<Request>()

function openStream(request: Request, feed?: (send: (text: string) => void) => void): Response {
  let closed = false
  serving.add(request)
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      request.signal.addEventListener('abort', () => {
        serving.delete(request)
        controller.error(request.signal.reason)
      })
      feed?.((text) => {
        // Nothing more can be sent once the reader has gone; a real server's send would fail too.
        if (!closed && !request.signal.aborted) controller.enqueue(encoder.encode(text))
      })
    },
    cancel() {
      closed = true
      serving.delete(request)
    },
  })
  return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
}

afterEach(() => {
  vi.useRealTimers()
})

describe('createSseParser', () => {
  it('gives each event once it is complete, whatever the pieces', () => {
    const parse = createSseParser()

    expect(parse('data: {"a"')).toEqual([])
    expect(parse(':1}\n')).toEqual([])
    expect(parse('\ndata: 2\n\ndata: 3')).toEqual(['{"a":1}', '2'])
    expect(parse('\n\n')).toEqual(['3'])
  })

  it('skips pings and other fields, and joins data lines', () => {
    const parse = createSseParser()

    expect(parse(': ping\n\nevent: x\nid: 1\ndata: one\ndata: two\n\n')).toEqual(['one\ntwo'])
  })

  it('takes every kind of line ending, even a \\r\\n split in two', () => {
    const parse = createSseParser()

    expect(parse('data: 1\r\n\r\ndata: 2\r\rdata: 3\r')).toEqual(['1', '2'])
    expect(parse('\n\r\n')).toEqual(['3'])
  })

  it('needs no space after the colon, and keeps any further ones', () => {
    expect(createSseParser()('data:1\n\ndata:   2\n\n')).toEqual(['1', '  2'])
  })

  it('drops an event that never ended', () => {
    const parse = createSseParser()

    expect(parse('data: half')).toEqual([])
    expect(parse('')).toEqual([])
  })
})

describe('askStream', () => {
  const final: StreamEvent = { type: 'final', reply: { ...repair, answer: 'Côte de Blaye — ✓' } }
  const whole = encoder.encode(sse([...repairStream, final]))

  /** Everything an ask leaves behind: the events through the reducer, and the reply. */
  async function finalCell(chunks: Uint8Array[]) {
    serve(() => chunked(chunks))
    const asked: Conversation = conversationReducer(
      { id: null, cells: [] },
      { type: 'asked', cellId: 'c1', question: repair.question },
    )
    let state = asked
    const reply = await askStream(
      { question: repair.question },
      {
        onEvent: (event) => {
          state = conversationReducer(state, { type: 'traced', cellId: 'c1', event })
        },
      },
    )
    return conversationReducer(state, { type: 'replied', cellId: 'c1', reply })
  }

  it.each([1, 2, 3, 7, 64, 1024])(
    'gets the same final state when the bytes arrive %i at a time',
    async (size) => {
      expect(await finalCell(split(whole, size))).toEqual(await finalCell([whole]))
    },
  )

  it('hands over every event in order, and keeps split characters whole', async () => {
    const cell = await finalCell(split(whole, 1))

    expect(cell.cells[0]?.trace).toEqual(repairStream)
    expect(cell.cells[0]?.reply).toMatchObject({ answer: 'Côte de Blaye — ✓' })
  })

  it('takes several events in one chunk, and pings between them', async () => {
    const text = `: ping\n\n${sse(repairStream.slice(0, 2))}: ping\n\n${sse([final])}`
    serve(() => chunked([encoder.encode(text)]))
    const onEvent = vi.fn<(event: TraceEvent) => void>()

    await askStream({ question: 'q' }, { onEvent })

    expect(onEvent).toHaveBeenCalledTimes(2)
  })

  it('posts the question to /api/ask/stream', async () => {
    let seen: unknown
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        seen = await request.json()
        return chunked([encoder.encode(sse([final]))])
      }),
    )

    await askStream({ question: 'q', conversation_id: null }, { onEvent: () => {} })

    expect(seen).toEqual({ question: 'q', conversation_id: null })
  })

  it('an error event fails with its code, message and wait, and never falls back', async () => {
    const error: StreamEvent = {
      type: 'error',
      error: { error: 'llm_rate_limited', message: 'Limit.', retry_after_seconds: 18 },
    }
    const calls = serve(() => chunked([encoder.encode(sse([repairStream[0]!, error]))]))

    const failure = await askStream({ question: 'q' }, { onEvent: () => {} }).catch(
      (e: unknown) => e,
    )

    expect(failure).toBeInstanceOf(ApiError)
    expect(failure).toMatchObject({
      code: 'llm_rate_limited',
      message: 'Limit.',
      retryAfterSeconds: 18,
    })
    expect(calls.plain).toBe(0)
  })

  it('an error as the very first event is the API speaking: no fallback', async () => {
    const error: StreamEvent = {
      type: 'error',
      error: { error: 'db_unavailable', message: 'No database.' },
    }
    const calls = serve(() => chunked([encoder.encode(sse([error]))]))

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'db_unavailable',
    })
    expect(calls.plain).toBe(0)
  })

  it('an API error before the stream starts is shown, not retried', async () => {
    const calls = serve(() =>
      HttpResponse.json({ error: 'invalid_request', message: 'Too long.' }, { status: 422 }),
    )

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'invalid_request',
      status: 422,
    })
    expect(calls.plain).toBe(0)
  })

  it('a reply with an unknown kind is rejected, like from ask()', async () => {
    const odd = { type: 'final', reply: { ...answer, kind: 'poem' } }
    serve(() => chunked([encoder.encode(`data: ${JSON.stringify(odd)}\n\n`)]))

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'bad_response',
    })
  })

  it('text that is not JSON is an unexpected response', async () => {
    serve(() => chunked([encoder.encode('data: <html>\n\n')]))

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'bad_response',
    })
  })
})

describe('the fallback to POST /ask', () => {
  it.each([
    ['a network failure', () => HttpResponse.error()],
    ['a gateway error without our body', () => new Response(null, { status: 502 })],
    ['a page instead of a stream', () => new Response('<html>', { status: 200 })],
    ['a stream with no events', () => chunked([])],
  ])('%s before any event asks once, and answers', async (_name, response) => {
    const calls = serve(response as () => Response)

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).resolves.toEqual(answer)

    expect(calls.plain).toBe(1)
  })

  it('after the first event there is no fallback: the run has started', async () => {
    const calls = serve(() => chunked([encoder.encode(sse([repairStream[0]!]))]))

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'stream_interrupted',
    })
    expect(calls.plain).toBe(0)
  })

  it('a failing fallback reports its own error', async () => {
    server.use(
      http.post(STREAM_URL, () => HttpResponse.error()),
      http.post(ASK_URL, () =>
        HttpResponse.json({ error: 'db_unavailable', message: 'No database.' }, { status: 503 }),
      ),
    )

    await expect(askStream({ question: 'q' }, { onEvent: () => {} })).rejects.toMatchObject({
      code: 'db_unavailable',
    })
  })
})

describe('stopping and timeouts', () => {
  /** fetch answers with an open stream; a second call (the fallback) is noted and answered. */
  function stubOpenStream(feed?: (send: (text: string) => void) => void) {
    const urls: string[] = []
    vi.stubGlobal('fetch', async (request: Request) => {
      urls.push(new URL(request.url).pathname)
      return request.url.endsWith('/stream') ? openStream(request, feed) : Response.json(answer)
    })
    return urls
  }

  it("the caller's abort is passed on as it is, with no fallback", async () => {
    const urls = stubOpenStream()
    const controller = new AbortController()

    const asked = askStream({ question: 'q' }, { signal: controller.signal, onEvent: () => {} })
    const failure = asked.catch((e: unknown) => e)
    await vi.waitFor(() => expect(urls).toHaveLength(1))
    await collectGarbage()
    controller.abort()

    expect(await failure).not.toBeInstanceOf(ApiError)
    expect(await failure).toMatchObject({ name: 'AbortError' })
    expect(urls).toEqual(['/api/ask/stream'])
  })

  it('aborting closes the connection', async () => {
    const closed = vi.fn<() => void>()
    let sent = false
    vi.stubGlobal('fetch', async (request: Request) => {
      sent = true
      request.signal.addEventListener('abort', closed)
      return openStream(request)
    })
    const controller = new AbortController()

    const failure = askStream(
      { question: 'q' },
      { signal: controller.signal, onEvent: () => {} },
    ).catch((e: unknown) => e)
    // Stop once the request is really on its way (authFetch reads the body first, U7.1).
    await vi.waitFor(() => expect(sent).toBe(true))
    controller.abort()
    await failure

    expect(closed).toHaveBeenCalledOnce()
  })

  it('silence before any event falls back to POST /ask', async () => {
    vi.useFakeTimers()
    const urls = stubOpenStream()

    const asked = askStream({ question: 'q' }, { onEvent: () => {} })
    await vi.advanceTimersByTimeAsync(STREAM_IDLE_TIMEOUT_MS)

    await expect(asked).resolves.toEqual(answer)
    expect(urls).toEqual(['/api/ask/stream', '/api/ask'])
  })

  it('silence after an event means the connection was lost', async () => {
    vi.useFakeTimers()
    const urls = stubOpenStream((send) => send(sse([repairStream[0]!])))

    const failure = askStream({ question: 'q' }, { onEvent: () => {} }).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(STREAM_IDLE_TIMEOUT_MS)

    expect(await failure).toMatchObject({ code: 'stream_interrupted' })
    expect(urls).toEqual(['/api/ask/stream'])
  })

  it('pings keep it alive, up to the total limit, which is a timeout and no fallback', async () => {
    vi.useFakeTimers()
    const urls = stubOpenStream((send) => {
      setInterval(() => send(': ping\n\n'), 15_000)
    })

    const failure = askStream({ question: 'q' }, { onEvent: () => {} }).catch((e: unknown) => e)
    await vi.advanceTimersByTimeAsync(119_000)
    expect(urls).toHaveLength(1) // well past the 45 s, thanks to the pings
    await vi.advanceTimersByTimeAsync(1_000)

    expect(await failure).toMatchObject({ code: 'api_timeout' })
    expect(urls).toEqual(['/api/ask/stream'])
  })
})
