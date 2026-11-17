import { HttpResponse } from 'msw'

import type { LimitName } from '@/api/client'

import messagesJson from './limits.json'

/**
 * Each limit's refusal as the real API words it (U7.5), with the default numbers (config.py).
 * test_web_fixtures.py checks this file against the API's own messages, so they can't drift.
 */
export const LIMIT_MESSAGES = messagesJson satisfies Record<LimitName, string>

// About what each limit makes you wait: under a minute for the per-minute ones, hours for the day's.
export const LIMIT_WAITS: Record<LimitName, number> = {
  questions_minute: 30,
  questions_day: 5 * 3600 + 20 * 60,
  questions_everyone: 2 * 3600 + 40 * 60,
  reruns: 30,
  logins: 40,
  signups: 3600,
  key_checks: 30,
  connection_checks: 30,
  member_adds: 30,
}

/** Mock mode only: "limit: questions_day" as a question shows that limit's refusal. */
export const LIMIT_PREFIX = /^limit:\s*(\w+)$/i

export function isLimitName(name: string | undefined): name is LimitName {
  return name !== undefined && name in LIMIT_MESSAGES
}

/** The 429 the API sends for this limit: its message, the wait (body and header) and its name. */
export function limitError(limit: LimitName) {
  const wait = LIMIT_WAITS[limit]
  return HttpResponse.json(
    { error: 'rate_limited', message: LIMIT_MESSAGES[limit], retry_after_seconds: wait, limit },
    { status: 429, headers: { 'Retry-After': String(wait) } },
  )
}
