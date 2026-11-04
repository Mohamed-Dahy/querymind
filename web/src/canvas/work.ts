import type { AskResponse } from '@/api/client'

export type FailedKind = AskResponse['attempts'][number]['kind']

/** One SQL try of an answer, in order. */
export type WorkTry = { sql: string; failed?: { kind: FailedKind; message: string }; used: boolean }

/**
 * Every try, the answer's last. When a repair of a suspicious result fails, the answer *is* that
 * suspicious query, so it isn't listed twice: its entry is the one that was used.
 */
/**
 * The explorer with this answer's tables lit: those its SQL read, and those shown to the LLM that it
 * didn't use. The whole schema shown says nothing, so it isn't sent. Null if there's nothing to light.
 */
export function tablesPath({ tables_used: used, schema_selection: selection }: AskResponse) {
  const shown =
    selection?.mode === 'retrieved' ? selection.tables.filter((table) => !used.includes(table)) : []
  if (used.length === 0 && shown.length === 0) return null
  const params = new URLSearchParams()
  if (used.length > 0) params.set('used', used.join(','))
  if (shown.length > 0) params.set('shown', shown.join(','))
  return `/explore?${params.toString()}`
}

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
