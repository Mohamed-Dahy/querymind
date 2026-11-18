/**
 * The workspace's audit log (U7.4, PLAN 5.5): who asked what, the SQL, and how it ended, plus every
 * Phase 5 action (logins, keys, databases, members, permissions). Admins only: the other roles get
 * 403. Newest first, a page at a time: the next page starts before the last line's id, so lines
 * written meanwhile don't shift it (no offset).
 */
import { useInfiniteQuery } from '@tanstack/react-query'

import { api, call } from './client'
import type { components, operations } from './schema'

type Schemas = components['schemas']
export type AuditLine = Schemas['AuditLine']
export type AuditPage = Schemas['AuditPage']
export type AuditAction = AuditLine['action']
type AuditQuery = NonNullable<operations['audit_log_admin_audit_get']['parameters']['query']>

/** What the filters can narrow to. Empty = no filter; the URL keeps them (AuditSettings). */
export type AuditFilters = {
  action?: AuditAction | undefined
  outcome?: string | undefined
  email?: string | undefined
  /** Found in the question or the SQL. */
  q?: string | undefined
}

const TIMEOUT_MS = 10_000
export const AUDIT_PAGE_SIZE = 50

/** The cache key of the audit log. Cleared with the workspace. */
export const AUDIT_QUERY = ['audit'] as const

export function getAuditPage(
  filters: AuditFilters,
  before: number | undefined,
  signal?: AbortSignal,
): Promise<AuditPage> {
  const query: AuditQuery = { limit: AUDIT_PAGE_SIZE }
  // Only the filters that are set: the API reads an empty string as "find nothing".
  if (filters.action) query.action = filters.action
  if (filters.outcome) query.outcome = filters.outcome
  if (filters.email) query.email = filters.email
  if (filters.q) query.q = filters.q
  if (before !== undefined) query.before = before
  return call(TIMEOUT_MS, signal, (s) => api.GET('/admin/audit', { params: { query }, signal: s }))
}

/** The pages loaded so far; `fetchNextPage` is "Load more". */
export function useAuditLog(filters: AuditFilters, { enabled = true } = {}) {
  return useInfiniteQuery({
    queryKey: [...AUDIT_QUERY, filters],
    queryFn: ({ pageParam, signal }) => getAuditPage(filters, pageParam, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (last) => (last.has_more ? last.lines.at(-1)?.id : undefined),
    // The list stays on screen while another filter loads, instead of a skeleton each time.
    placeholderData: (previous) => previous,
    enabled,
  })
}
