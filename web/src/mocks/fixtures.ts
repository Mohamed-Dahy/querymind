import type {
  AskResponse,
  ClarificationResponse,
  HealthResponse,
  RefusalResponse,
} from '@/api/client'
import type { TraceEvent } from '@/api/stream'
import openapi from '../../openapi.json'
import errorsJson from './errors.json'
import answerJson from './recorded/answer.json'
import clarificationJson from './recorded/clarification.json'
import followupJson from './recorded/followup.json'
import monthlyJson from './recorded/monthly.json'
import refusalJson from './recorded/refusal.json'
import repairJson from './recorded/repair.json'
import singleJson from './recorded/single.json'
import answerStreamJson from './recorded/streams/answer.json'
import clarificationStreamJson from './recorded/streams/clarification.json'
import followupStreamJson from './recorded/streams/followup.json'
import monthlyStreamJson from './recorded/streams/monthly.json'
import refusalStreamJson from './recorded/streams/refusal.json'
import repairStreamJson from './recorded/streams/repair.json'
import singleStreamJson from './recorded/streams/single.json'

// Real POST /ask replies, recorded once by scripts/record_fixtures.py. JSON imports get loose types
// (`kind: string`), so they are cast; tests/unit/test_web_fixtures.py checks every file against the
// Pydantic models, so the casts are safe.
export const answer = answerJson as AskResponse
export const followup = followupJson as AskResponse
export const clarification = clarificationJson as ClarificationResponse
export const refusal = refusalJson as RefusalResponse
// U1.5's chart cases: sales per month in 1997 (month is a number 1–12, so a line chart), and one
// number (no chart).
export const monthly = monthlyJson as AskResponse
export const single = singleJson as AskResponse
// A repair, from a scripted LLM on the real pipeline and database (scripts/record_fixtures.py).
export const repair = repairJson as AskResponse

// The events before each reply: what POST /ask/stream sent. The same tests check these files.
export const answerStream = answerStreamJson as TraceEvent[]
export const followupStream = followupStreamJson as TraceEvent[]
export const clarificationStream = clarificationStreamJson as TraceEvent[]
export const refusalStream = refusalStreamJson as TraceEvent[]
export const monthlyStream = monthlyStreamJson as TraceEvent[]
export const singleStream = singleStreamJson as TraceEvent[]
export const repairStream = repairStreamJson as TraceEvent[]

/**
 * A big result for the table (U1.4): the recorded answer's shape with `count` made-up rows, and a
 * null now and then. Built from the typed fixture, so it stays an AskResponse.
 */
export function answerWithRows(count: number): AskResponse {
  const rows = Array.from({ length: count }, (_, index) => [
    `Category ${index + 1}`,
    index % 50 === 49 ? null : ((index * 7919) % 100_000) + 0.25,
  ])
  return { ...answer, rows, row_count: count, truncated: count >= 1000 }
}

// The version comes from the exported schema, so it changes with the API's.
export const health: HealthResponse = { status: 'ok', version: openapi.info.version }

export type MockError = { status: number; message: string; retry_after_seconds?: number }

/** Hand-made: one per error code the API sends, with its real HTTP status (api/main.py). */
export const errors = errorsJson satisfies Record<string, MockError>
export type MockErrorCode = keyof typeof errors
