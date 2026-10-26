import { http, HttpResponse } from 'msw'

import type { AskReply, AskRequest } from '@/api/client'
import {
  answer,
  clarification,
  errors,
  followup,
  health,
  refusal,
  type MockErrorCode,
} from './fixtures'

// The paths the browser calls. MSW answers before Vite's proxy, so the /api prefix is still there.
export const HEALTH_URL = '/api/health'
export const ASK_URL = '/api/ask'

/** The API's error body with its real status, e.g. apiError('db_unavailable') → 503. */
export function apiError(code: MockErrorCode) {
  const { status, message } = errors[code]
  return HttpResponse.json({ error: code, message }, { status })
}

// Mock mode only: typing "error: db_unavailable" shows that error, to design the error states.
const ERROR_PREFIX = /^error:\s*(\w+)$/i
const CHANGES_DATA = /\b(delete|drop|update|insert|truncate|alter)\b/i
const AMBIGUOUS = /\b(popular|best)\b/i

/**
 * A recorded reply that fits the question, so mock mode feels like the real API: a follow-up
 * continues, "delete…" is refused, "best…" asks back, anything else gets the recorded answer.
 */
export function replyFor(request: AskRequest): AskReply {
  const { question, conversation_id } = request
  if (conversation_id) return { ...followup, question, conversation_id }
  if (CHANGES_DATA.test(question)) return { ...refusal, question }
  if (AMBIGUOUS.test(question)) return { ...clarification, question }
  return { ...answer, question }
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
