/**
 * The workspace's members (U7.1d, PLAN 5.12): admins add people who have signed up, change their
 * role, and remove them. A change applies at the member's next request (the API reads the role on
 * every request). Admins only: the other roles get 403.
 */
import { useQuery } from '@tanstack/react-query'

import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type MemberInfo = Schemas['MemberInfo']
export type Role = MemberInfo['role']

const TIMEOUT_MS = 10_000

/** The cache key of the members list. Cleared with the workspace. */
export const MEMBERS_QUERY = ['members'] as const

export function listMembers(signal?: AbortSignal): Promise<MemberInfo[]> {
  return call(TIMEOUT_MS, signal, (s) => api.GET('/admin/members', { signal: s })).then(
    ({ members }) => members,
  )
}

/** Add someone who has signed up. Throws ApiError: account_not_found, already_member, rate_limited. */
export function addMember(email: string, role: Role): Promise<MemberInfo> {
  return call(TIMEOUT_MS, undefined, (s) =>
    api.POST('/admin/members', { body: { email, role }, signal: s }),
  )
}

/** Give a member another role. Throws ApiError: last_admin (the workspace needs one), member_not_found. */
export function changeRole(userId: string, role: Role): Promise<MemberInfo> {
  return call(TIMEOUT_MS, undefined, (s) =>
    api.PATCH('/admin/members/{user_id}', {
      params: { path: { user_id: userId } },
      body: { role },
      signal: s,
    }),
  )
}

/** Take a member out of the workspace (yourself too, if another admin stays). */
export function removeMember(userId: string): Promise<void> {
  return call(TIMEOUT_MS, undefined, (s) =>
    api.DELETE('/admin/members/{user_id}', {
      params: { path: { user_id: userId } },
      signal: s,
    }),
  )
}

export function useMembers({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: MEMBERS_QUERY,
    queryFn: ({ signal }) => listMembers(signal),
    enabled,
  })
}
