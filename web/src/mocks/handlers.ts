import { delay, http, HttpResponse } from 'msw'

import {
  addSavedQuestion,
  deleteStored,
  getStored,
  listSavedQuestions,
  listStored,
  questionsUsing,
  recordTurn,
  removeSavedQuestion,
  rerunStored,
  updateStored,
} from './conversations'

import type { AskReply, AskRequest } from '@/api/client'
import type { ConversationUpdate } from '@/api/conversations'
import type { StreamEvent, TraceEvent } from '@/api/stream'
import {
  answer,
  answerStream,
  answerWithRows,
  catalog,
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
  semantic,
  single,
  singleStream,
  type MockError,
  type MockErrorCode,
} from './fixtures'

// The paths the browser calls. MSW answers before Vite's proxy, so the /api prefix is still there.
export const HEALTH_URL = '/api/health'
export const ASK_URL = '/api/ask'
export const STREAM_URL = '/api/ask/stream'
const CONVERSATION_URL = '/api/conversations/:id'
export const SAVED_QUESTIONS_URL = '/api/saved-questions'

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
  recordTurn(reply, events) // the real API saves before the final event, too
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
    const reply = replyFor(body)
    recordTurn(reply, [])
    return HttpResponse.json(reply)
  }),
  http.get('/api/conversations', ({ request }) => {
    const { searchParams } = new URL(request.url)
    return HttpResponse.json(listStored(searchParams.get('q'), Number(searchParams.get('limit'))))
  }),
  http.get(CONVERSATION_URL, ({ params }) => {
    const found = getStored(String(params['id']))
    return found ? HttpResponse.json(found) : apiError('conversation_not_found')
  }),
  http.patch(CONVERSATION_URL, async ({ params, request }) => {
    const change = (await request.json()) as ConversationUpdate
    const found = updateStored(String(params['id']), change)
    return found ? HttpResponse.json(found) : apiError('conversation_not_found')
  }),
  http.delete(CONVERSATION_URL, ({ params }) =>
    deleteStored(String(params['id']))
      ? new HttpResponse(null, { status: 204 })
      : apiError('conversation_not_found'),
  ),
  http.post(`${CONVERSATION_URL}/turns/:n/rerun`, ({ params }) => {
    const result = rerunStored(String(params['id']), Number(params['n']))
    return result ? HttpResponse.json(result) : apiError('turn_not_found')
  }),
  http.get('/api/catalog', () => HttpResponse.json(catalog)),
  http.get('/api/semantic', () => HttpResponse.json(semantic)),
  http.get('/api/catalog/tables/:table/questions', ({ params }) =>
    HttpResponse.json(questionsUsing(String(params['table']))),
  ),
  http.get(SAVED_QUESTIONS_URL, () => HttpResponse.json({ questions: listSavedQuestions() })),
  http.post(SAVED_QUESTIONS_URL, async ({ request }) => {
    const { question } = (await request.json()) as { question: string }
    return HttpResponse.json(addSavedQuestion(question.trim()))
  }),
  http.delete(`${SAVED_QUESTIONS_URL}/:id`, ({ params }) => {
    removeSavedQuestion(String(params['id']))
    return new HttpResponse(null, { status: 204 })
  }),
]
