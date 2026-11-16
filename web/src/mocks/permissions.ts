/**
 * The fake API's permissions (U7.2), in localStorage like the members: each database's rule per
 * role (null = every table), and the rows the fake login sees in Acme Sales, where it is a viewer
 * scoped to the Eastern region. Its answers that read a row-secured table (Northwind's orders and
 * order_details, as on the real demo) say so, as the real API does.
 */
import type { AskReply } from '@/api/client'
import type { DatabaseId } from '@/api/explorer'
import type { RestrictedRole, TableAccessInfo } from '@/api/permissions'
import type { Me } from '@/api/session'

import { catalog } from './fixtures'
import { currentMockUser, OTHER_WORKSPACE } from './session'
import { savedDatabaseCatalog } from './settings'

const KEY = 'querymind-mock-table-access'

type Rule = string[] | null
type Rules = Record<string, Record<RestrictedRole, Rule>>

/** The tables with row security on the demo (data/seed_northwind.py's policies). */
export const ROW_SECURED = ['orders', 'order_details']
/** The fake login's rows in Acme Sales. */
export const ACME_SCOPE = ['Eastern']

function loadRules(): Rules {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Rules
  } catch {
    return {}
  }
}

const keyOf = (database: DatabaseId) => database ?? 'demo'

function tablesOf(database: DatabaseId): string[] {
  const tables = database === null ? catalog.tables : savedDatabaseCatalog().tables
  return tables.map(({ name }) => name).sort()
}

/** GET /table-access: the database's tables and each role's rule. */
export function mockTableAccess(database: DatabaseId): TableAccessInfo {
  const rules = loadRules()[keyOf(database)] ?? { analyst: null, viewer: null }
  return { connection_id: database, tables: tablesOf(database), ...rules }
}

/** PUT /table-access, as the real API: a table the database lacks is refused (422). */
export function setMockTableAccess(
  database: DatabaseId,
  role: RestrictedRole,
  tables: Rule,
): TableAccessInfo | { refused: string } {
  const known = new Set(tablesOf(database))
  const unknown = (tables ?? []).find((table) => !known.has(table))
  if (unknown !== undefined) return { refused: `This database has no table called ${unknown}.` }
  const rules = loadRules()
  const current = rules[keyOf(database)] ?? { analyst: null, viewer: null }
  rules[keyOf(database)] = { ...current, [role]: tables === null ? null : [...new Set(tables)] }
  localStorage.setItem(KEY, JSON.stringify(rules))
  return mockTableAccess(database)
}

/** The tables this role may use on the database: null = every table (admins always). */
export function mockAllowed(database: DatabaseId, role: Me['role']): Rule {
  if (role === 'admin') return null
  return mockTableAccess(database)[role]
}

/** The rows the fake login sees now: Eastern in Acme Sales, every row in its own workspace. */
export function mockScope(): string[] | null {
  return currentMockUser().workspace.id === OTHER_WORKSPACE.id ? ACME_SCOPE : null
}

/** A recorded reply as the real API would send it to this user: limited rows say which. */
export function withMockScope<Reply extends AskReply>(reply: Reply): Reply {
  const scope = mockScope()
  if (reply.kind !== 'answer' || scope === null) return reply
  const secured = reply.tables_used.some((table) => ROW_SECURED.includes(table))
  return secured ? { ...reply, row_scope: scope } : reply
}
