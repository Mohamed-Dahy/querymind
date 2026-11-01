import { delay, http, HttpResponse } from 'msw'

import type { AskReply, AskRequest } from '@/api/client'
import type { StreamEvent, TraceEvent } from '@/api/stream'
import {
  answer,
  answerStream,
  answerWithRows,
  clarification,
  clarificationStream,
  errors,
  followup,
  followupStream,
  health,
  monthly,
  monthlyStream,
  refusal,
  refusalStream,
  repair,
  repairStream,
  single,
  singleStream,
  type MockError,
  type MockErrorCode,
} from './fixtures'

// The paths the browser calls. MSW answers before Vite's proxy, so the /api prefix is still there.
export const HEALTH_URL = '/api/health'
export const ASK_URL = '/api/ask'
export const STREAM_URL = '/api/ask/stream'

// In the browser's mock mode the steps come one by one, so they can be seen. Tests don't wait.
const EVENT_DELAY_MS = import.meta.env.MODE === 'mock' ? 60 : 0

/** The API's error body with its real status, e.g. apiError('db_unavailable') → 503. */
export function apiError(code: MockErrorCode) {
  const { status, message, retry_after_seconds: wait }: MockError = errors[code]
  if (wait === undefined) return HttpResponse.json({ error: code, message }, { status })
  return HttpResponse.json(
    { error: code, message, retry_after_seconds: wait },
    { status, headers: { 'Retry-After': String(wait) } },
  )
}

// Mock mode only: typing "error: db_unavailable" shows that error, to design the error states.
const ERROR_PREFIX = /^error:\s*(\w+)$/i
const CHANGES_DATA = /\b(delete|drop|update|insert|truncate|alter)\b/i
const AMBIGUOUS = /\b(popular|best)\b/i
// Mock mode only: "rows: 1000" answers with that many made-up rows, to try the table (max 1,000).
const MANY_ROWS = /\brows:\s*(\d+)\b/i
// The two chart cases (U1.5). Only "how many customers…": "How many orders did each country place?"
// must still get the bar-chart answer.
const PER_MONTH = /\bper month\b|\bmonthly\b/i
const ONE_NUMBER = /^how many customers\b/i
// U2's repair loop. "in each": "How many orders did each country place?" stays a plain answer.
const NEEDS_REPAIR = /\bin each country\b/i
// Mock mode only: "stream: broken" makes the stream fail at once, so the UI falls back to POST /ask.
const BROKEN_STREAM = /\bstream:\s*broken\b/i
// Mock mode only: "stream: stuck" sends a few steps and then waits forever, to try the Stop button.
const STUCK_STREAM = /\bstream:\s*stuck\b/i

/**
 * A recorded reply and its events that fit the question, so mock mode feels like the real API: a
 * follow-up continues, "delete…" is refused, "best…" asks back, anything else gets the recorded
 * answer. Like the real API, a question without a conversation starts a new one with a fresh id
 * (the recorded id would make every new conversation the same one on the canvas).
 */
function recordedFor(request: AskRequest): { reply: AskReply; events: TraceEvent[] } {
  const { question } = request
  const many = MANY_ROWS.exec(question)?.[1]
  if (many) {
    const conversation_id = request.conversation_id ?? crypto.randomUUID()
    const reply = { ...answerWithRows(Math.min(Number(many), 1000)), question, conversation_id }
    return { reply, events: answerStream }
  }
  if (request.conversation_id) {
    const reply = { ...followup, question, conversation_id: request.conversation_id }
    return { reply, events: followupStream }
  }
  const conversation_id = crypto.randomUUID()
  const fitted = <Reply extends AskReply>(recorded: Reply, events: TraceEvent[]) => ({
    reply: { ...recorded, question, conversation_id },
    events,
  })
  if (CHANGES_DATA.test(question)) return fitted(refusal, refusalStream)
  if (AMBIGUOUS.test(question)) return fitted(clarification, clarificationStream)
  if (PER_MONTH.test(question)) return fitted(monthly, monthlyStream)
  if (NEEDS_REPAIR.test(question)) return fitted(repair, repairStream)
  if (ONE_NUMBER.test(question)) return fitted(single, singleStream)
  return fitted(answer, answerStream)
}

export function replyFor(request: AskRequest): AskReply {
  return recordedFor(request).reply
}

/**
 * These events as POST /ask/stream sends them: one `data:` line each, then a blank line. `stuck`: it
 * stays open afterwards, like a step that is taking a long time.
 */
export function streamOf(events: readonly StreamEvent[], { stuck = false } = {}) {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const event of events) {
        if (EVENT_DELAY_MS > 0) await delay(EVENT_DELAY_MS)
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      }
      if (!stuck) controller.close()
    },
  })
  return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

/** The whole recorded stream for a question: its events, then the reply. */
export function replyStream(request: AskRequest) {
  const { reply, events } = recordedFor(request)
  return streamOf([...events, { type: 'final', reply }])
}

/** A run that fails partway: the first steps, then the API's error, with status 200 already sent. */
export function errorStream(code: MockErrorCode) {
  const { message, retry_after_seconds: wait }: MockError = errors[code]
  return streamOf([
    ...answerStream.slice(0, 4),
    {
      type: 'error',
      error: { error: code, message, ...(wait !== undefined && { retry_after_seconds: wait }) },
    },
  ])
}

export const handlers = [
  http.get(HEALTH_URL, () => HttpResponse.json(health)),
  http.post(STREAM_URL, async ({ request }) => {
    const body = (await request.json()) as AskRequest
    if (BROKEN_STREAM.test(body.question)) return HttpResponse.error()
    if (STUCK_STREAM.test(body.question)) return streamOf(answerStream.slice(0, 5), { stuck: true })
    const code = ERROR_PREFIX.exec(body.question)?.[1]?.toLowerCase()
    if (code && code in errors) return errorStream(code as MockErrorCode)
    return replyStream(body)
  }),
  http.post(ASK_URL, async ({ request }) => {
    const body = (await request.json()) as AskRequest
    const code = ERROR_PREFIX.exec(body.question)?.[1]?.toLowerCase()
    if (code && code in errors) return apiError(code as MockErrorCode)
    return HttpResponse.json(replyFor(body))
  }),
]
