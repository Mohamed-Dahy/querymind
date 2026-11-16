import { Database, KeyRound, ShieldCheck, Users, type LucideIcon } from 'lucide-react'

import type { Me } from '@/api/session'

export type SettingsTab = {
  /** The tab's own URL, under /settings. */
  path: string
  label: string
  Icon: LucideIcon
  /** Words the command palette finds it by. */
  keywords: readonly string[]
  /** Hidden from the other roles (the audit log). Read-only tabs stay visible to everyone. */
  adminOnly?: boolean
}

/**
 * The Settings page's tabs, in the order of the side list. Each step of Phase 5's screens adds its
 * own (U7.1b–U7.4): databases, members, permissions, then the audit log.
 */
export const SETTINGS_TABS: readonly SettingsTab[] = [
  {
    path: 'key',
    label: 'LLM key',
    Icon: KeyRound,
    keywords: ['api key', 'model', 'provider', 'groq', 'gemini', 'openai', 'cost'],
  },
  {
    path: 'databases',
    label: 'Databases',
    Icon: Database,
    keywords: ['connection', 'postgres', 'own database', 'add database', 'read-only'],
  },
  {
    path: 'members',
    label: 'Members',
    Icon: Users,
    keywords: ['team', 'people', 'add member', 'role', 'admin', 'analyst', 'viewer'],
    // The members list is admins' only (the API answers 403 to the other roles).
    adminOnly: true,
  },
  {
    path: 'permissions',
    label: 'Permissions',
    Icon: ShieldCheck,
    keywords: ['access', 'tables', 'rows', 'restrict', 'analyst', 'viewer', 'table access'],
    adminOnly: true,
  },
]

/** The tabs this role may open. */
export function tabsFor(role: Me['role'] | undefined): SettingsTab[] {
  return SETTINGS_TABS.filter((tab) => !tab.adminOnly || role === 'admin')
}

/** A saved database's descriptions, to review (U7.1c): a page under the Databases tab. */
export const reviewPath = (id: string) => `/settings/databases/${id}/dictionary`
