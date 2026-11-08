/**
 * POST /ask/stream: the same question as `ask()` in client.ts, but the agent's steps arrive as they
 * happen. It is read with `fetch`, because the browser's own EventSource can only send a GET.
 */
import {
  ApiError,
  ask,
  ASK_TIMEOUT_MS,
  ASK_URL,
  checkedReply,
  errorFrom,
  isErrorBody,
} from './client'
import type { AskReply, AskRequest } from './client'
import { authFetch } from './session'
import type { components } from './schema'

// Listed by hand, because openapi-typescript can't read FastAPI's stream description. The members
// are still the generated types, and trace.ts's exhaustive switch fails tsc when one is missing.
type Schemas = components['schemas']
export type TraceEvent =
  | Schemas['StepEvent']
  | Schemas['SchemaEvent']
  | Schemas['RewriteEvent']
  | Schemas['ExamplesEvent']
  | Schemas['SqlEvent']
  | Schemas['ValidatedEvent']
  | Schemas['AttemptFailedEvent']
  | Schemas['CheckedEvent']
export type StreamEvent = TraceEvent | Schemas['FinalEvent'] | Schemas['ErrorEvent']

// The API sends a ping every 15 s while it works. This long with no bytes at all means a dead line.
export const STREAM_IDLE_TIMEOUT_MS = 45_000

/**
 * Server-Sent Events text → the `data` of each complete event. Feed it pieces of any size: what
 * can't be finished yet waits for the next piece. Comments (the pings) and other fields are skipped.
 */
export function createSseParser(): (text: string) => string[] {
  let buffer = ''
  let data: string[] = []
  return (text) => {
    buffer += text
    // A line ends with \n, \r\n or \r. A \r at the very end may be half of a \r\n, so it waits.
    const lines = buffer.split(/\r\n|\n|\r(?!$)/)
    buffer = lines.pop() ?? ''
    const events: string[] = []
    for (const line of lines) {
      if (line === '') {
        if (data.length > 0) events.push(data.join('\n'))
        data = []
      } else if (line.startsWith('data:')) {
        data.push(line.slice('data:'.length).replace(/^ /, ''))
      }
    }
    return events
  }
}

/** The stream broke without the API saying why: nothing we can show, and maybe worth a fallback. */
class StreamBroken extends Error {}

const TOTAL_TIMEOUT = 'total'
const IDLE_TIMEOUT = 'idle'

async function readStream(
  body: AskRequest,
  signal: AbortSignal | undefined,
  onEvent: (event: TraceEvent) => void,
): Promise<AskReply> {
  // Our own timers (not AbortSignal.timeout), so tests can fast-forward them.
  const timeout = new AbortController()
  const total = setTimeout(() => timeout.abort(TOTAL_TIMEOUT), ASK_TIMEOUT_MS)
  let idle = setTimeout(() => timeout.abort(IDLE_TIMEOUT), STREAM_IDLE_TIMEOUT_MS)
  const combined = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined

  try {
    // authFetch: with the access token, refreshed once on a 401 (U7.1).
    const response = await authFetch(
      new Request(`${ASK_URL}/stream`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: combined,
      }),
    )
    if (!response.ok) {
      const error: unknown = await response.json().catch(() => undefined)
      // Our API said why (a bad question, say). Anything else came from something in between.
      if (isErrorBody(error)) throw errorFrom(response.status, error)
      throw new StreamBroken(`HTTP ${response.status}`)
    }
    if (!response.headers.get('Content-Type')?.includes('text/event-stream') || !response.body) {
      throw new StreamBroken('not an event stream')
    }

    reader = response.body.getReader()
    // stream: true keeps a character split between two chunks for the next one.
    const decoder = new TextDecoder()
    const parse = createSseParser()
    for (;;) {
      const { done, value } = await reader.read()
      if (done) throw new StreamBroken('it ended without a final event')
      clearTimeout(idle) // any bytes count, pings too
      idle = setTimeout(() => timeout.abort(IDLE_TIMEOUT), STREAM_IDLE_TIMEOUT_MS)
      for (const data of parse(decoder.decode(value, { stream: true }))) {
        const event = JSON.parse(data) as StreamEvent
        if (event.type === 'final') return checkedReply(event.reply)
        if (event.type === 'error') throw errorFrom(200, event.error)
        onEvent(event)
      }
    }
  } catch (error) {
    if (signal?.aborted) throw error // the caller's own cancel: passed on, not a failure
    if (timeout.signal.reason === TOTAL_TIMEOUT) {
      throw new ApiError('api_timeout', 'The API took too long to answer.')
    }
    if (error instanceof ApiError) throw error
    if (error instanceof SyntaxError) {
      throw new ApiError('bad_response', 'The API sent an unexpected response.')
    }
    throw new StreamBroken(String(error))
  } finally {
    clearTimeout(total)
    clearTimeout(idle)
    // Closes the connection. After an answer that does nothing; after a failure, it stops the run.
    void reader?.cancel().catch(() => {})
  }
}

export type AskStreamOptions = {
  signal?: AbortSignal | undefined
  /** Called with each step, as it happens. */
  onEvent: (event: TraceEvent) => void
}

/**
 * Ask a question and hear each step. Resolves with the `POST /ask` reply, and rejects with an
 * ApiError (or, for the caller's own cancel, its AbortError).
 *
 * If the stream breaks before its first event (a proxy that can't stream, say), it asks `POST /ask`
 * once instead. After an event it never does: the run has started, and each one costs LLM quota.
 */
export async function askStream(
  body: AskRequest,
  { signal, onEvent }: AskStreamOptions,
): Promise<AskReply> {
  let started = false
  try {
    return await readStream(body, signal, (event) => {
      started = true
      onEvent(event)
    })
  } catch (error) {
    if (!(error instanceof StreamBroken)) throw error
    if (started) {
      throw new ApiError(
        'stream_interrupted',
        'The connection to the API was lost before the answer arrived.',
      )
    }
    return ask(body, signal)
  }
}
