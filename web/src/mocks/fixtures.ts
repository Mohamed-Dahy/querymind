import type {
  AskResponse,
  ClarificationResponse,
  HealthResponse,
  RefusalResponse,
} from '@/api/client'
import openapi from '../../openapi.json'
import errorsJson from './errors.json'
import answerJson from './recorded/answer.json'
import clarificationJson from './recorded/clarification.json'
import followupJson from './recorded/followup.json'
import refusalJson from './recorded/refusal.json'

// Real POST /ask replies, recorded once by scripts/record_fixtures.py. JSON imports get loose types
// (`kind: string`), so they are cast; tests/unit/test_web_fixtures.py checks every file against the
// Pydantic models, so the casts are safe.
export const answer = answerJson as AskResponse
export const followup = followupJson as AskResponse
export const clarification = clarificationJson as ClarificationResponse
export const refusal = refusalJson as RefusalResponse

// The version comes from the exported schema, so it changes with the API's.
export const health: HealthResponse = { status: 'ok', version: openapi.info.version }

/** Hand-made: one per error code the API sends, with its real HTTP status (api/main.py). */
export const errors = errorsJson satisfies Record<string, { status: number; message: string }>
export type MockErrorCode = keyof typeof errors
