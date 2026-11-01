import { http, HttpResponse } from 'msw'

import type { AskReply, AskRequest } from '@/api/client'
import {
  answer,
  answerWithRows,
  clarification,
  errors,
  followup,
  health,
  monthly,
  refusal,
  single,
  type MockError,
  type MockErrorCode,
} from './fixtures'

// The paths the browser calls. MSW answers before Vite's proxy, so the /api prefix is still there.
export const HEALTH_URL = '/api/health'
export const ASK_URL = '/api/ask'

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

/**
 * A recorded reply that fits the question, so mock mode feels like the real API: a follow-up
 * continues, "delete…" is refused, "best…" asks back, anything else gets the recorded answer.
 * Like the real API, a question without a conversation starts a new one with a fresh id (the
 * recorded id would make every new conversation the same one on the canvas).
 */
export function replyFor(request: AskRequest): AskReply {
  const { question } = request
  const many = MANY_ROWS.exec(question)?.[1]
  if (many) {
    const conversation_id = request.conversation_id ?? crypto.randomUUID()
    return { ...answerWithRows(Math.min(Number(many), 1000)), question, conversation_id }
  }
  if (request.conversation_id) {
    return { ...followup, question, conversation_id: request.conversation_id }
  }
  const conversation_id = crypto.randomUUID()
  if (CHANGES_DATA.test(question)) return { ...refusal, question, conversation_id }
  if (AMBIGUOUS.test(question)) return { ...clarification, question, conversation_id }
  if (PER_MONTH.test(question)) return { ...monthly, question, conversation_id }
  if (ONE_NUMBER.test(question)) return { ...single, question, conversation_id }
  return { ...answer, question, conversation_id }
}

export const handlers = [
  http.get(HEALTH_URL, () => HttpResponse.json(health)),
  http.post(ASK_URL, async ({ request }) => {
    const body = (await request.json()) as AskRequest
    const code = ERROR_PREFIX.exec(body.question)?.[1]?.toLowerCase()
    if (code && code in errors) return apiError(code as MockErrorCode)
    return HttpResponse.json(replyFor(body))
  }),
]
