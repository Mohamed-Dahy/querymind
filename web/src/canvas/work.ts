import type { AskResponse } from '@/api/client'

export type FailedKind = AskResponse['attempts'][number]['kind']

/** One SQL try of an answer, in order. */
export type WorkTry = { sql: string; failed?: { kind: FailedKind; message: string }; used: boolean }

/**
 * Every try, the answer's last. When a repair of a suspicious result fails, the answer *is* that
 * suspicious query, so it isn't listed twice: its entry is the one that was used.
 */
export function triesOf(reply: AskResponse): WorkTry[] {
  const tries: WorkTry[] = reply.attempts.map(({ sql, kind, message }) => ({
    sql,
    failed: { kind, message },
    used: false,
  }))
  const last = tries.at(-1)
  if (last?.failed?.kind === 'suspicious' && last.sql === reply.sql) {
    last.used = true
    return tries
  }
  return [...tries, { sql: reply.sql, used: true }]
}
