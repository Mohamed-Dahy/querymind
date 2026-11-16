/**
 * The fake API's workspace members (U7.1d), in localStorage like the saved databases: you (the
 * admin), an analyst whose rows are scoped to one region, and a viewer. A few emails show each
 * refusal the add form must handle; any other email is "someone who has signed up".
 *
 * Messages as recorded from the real API on 2026-11-16 (live, two accounts).
 */
import type { MemberInfo, Role } from '@/api/members'

import { mockUser } from './session'

const KEY = 'querymind-mock-members'

/** Emails to type in the add form to see a refusal. */
export const MEMBER_TRIGGERS = {
  /** → `account_not_found` (404): they need to sign up first */
  unknownEmail: 'nobody@example.com',
  /** → `rate_limited` (429, wait 30 s) */
  tooManyEmail: 'too-many@example.com',
} as const

export const MEMBER_MESSAGES = {
  account_not_found: 'No account has this email: they need to sign up first.',
  already_member: 'They are already a member of this workspace.',
  last_admin: 'A workspace needs an admin: make someone else an admin first.',
  member_not_found: 'This workspace has no such member.',
  rate_limited: 'Too many people added in a short time. Please wait before adding more.',
} as const

function seed(): MemberInfo[] {
  return [
    {
      user_id: mockUser.id,
      email: mockUser.email,
      role: 'admin',
      row_scope: null,
      added_at: '2026-11-02T09:00:00Z',
    },
    {
      user_id: '5f0d6c1e-0000-4000-8000-000000000011',
      email: 'sam@example.com',
      role: 'analyst',
      row_scope: ['Eastern'],
      added_at: '2026-11-09T14:30:00Z',
    },
    {
      user_id: '5f0d6c1e-0000-4000-8000-000000000012',
      email: 'lee@example.com',
      role: 'viewer',
      row_scope: null,
      added_at: '2026-11-12T08:15:00Z',
    },
  ]
}

export function loadMockMembers(): MemberInfo[] {
  try {
    const stored = localStorage.getItem(KEY)
    return stored === null ? seed() : (JSON.parse(stored) as MemberInfo[])
  } catch {
    return seed()
  }
}

function saveAll(members: MemberInfo[]) {
  localStorage.setItem(KEY, JSON.stringify(members))
}

type Refusal = keyof typeof MEMBER_MESSAGES

export function addMockMember(email: string, role: Role): MemberInfo | Refusal {
  const address = email.trim().toLowerCase() // accounts are stored lower-case
  if (address === MEMBER_TRIGGERS.unknownEmail) return 'account_not_found'
  if (address === MEMBER_TRIGGERS.tooManyEmail) return 'rate_limited'
  const all = loadMockMembers()
  if (all.some((member) => member.email === address)) return 'already_member'
  const added: MemberInfo = {
    user_id: crypto.randomUUID(),
    email: address,
    role,
    row_scope: null,
    added_at: new Date().toISOString(),
  }
  saveAll([...all, added])
  return added
}

/** Is `userId` the last admin (whom the workspace can't lose)? */
function lastAdmin(all: MemberInfo[], userId: string): boolean {
  const admins = all.filter(({ role }) => role === 'admin')
  return admins.length === 1 && admins[0]!.user_id === userId
}

export function setMockRole(userId: string, role: Role): MemberInfo | Refusal {
  const all = loadMockMembers()
  const member = all.find((one) => one.user_id === userId)
  if (!member) return 'member_not_found'
  if (role !== 'admin' && lastAdmin(all, userId)) return 'last_admin'
  const changed = { ...member, role }
  saveAll(all.map((one) => (one.user_id === userId ? changed : one)))
  return changed
}

export function removeMockMember(userId: string): MemberInfo | Refusal {
  const all = loadMockMembers()
  const member = all.find((one) => one.user_id === userId)
  if (!member) return 'member_not_found'
  if (lastAdmin(all, userId)) return 'last_admin'
  saveAll(all.filter((one) => one.user_id !== userId))
  return member
}
