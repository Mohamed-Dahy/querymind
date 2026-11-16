/**
 * Permissions (U7.2): which tables the analyst and viewer roles may use, per database (PLAN 5.2),
 * and which rows each member sees (5.3). Admins only, and admins themselves are never restricted.
 */
import { useQuery } from '@tanstack/react-query'

import { api, call } from './client'
import type { DatabaseId } from './explorer'
import type { components } from './schema'

type Schemas = components['schemas']
export type TableAccessInfo = Schemas['TableAccessInfo']
export type RestrictedRole = Schemas['TableAccessUpdate']['role']
export type RowScopeInfo = Schemas['RowScopeInfo']

// Reading a saved database's tables connects to it (10 s at most) the first time.
const READ_TIMEOUT_MS = 20_000
const WRITE_TIMEOUT_MS = 10_000

/** The demo's tags (5.3): the Northwind sales region of the employee who took the order. */
export const DEMO_TAGS = ['Eastern', 'Western', 'Northern', 'Southern'] as const
/** The API's limits on a tag (safety/row_scope.py): typed tags are checked before sending. */
export const TAG_MAX_LENGTH = 100
export const TAGS_MAX = 50
export const TAG_RESERVED = [',', '*'] as const

/** Why a typed tag can't be used (the API's rules, safety/row_scope.py), or null. */
export function tagProblem(tag: string): string | null {
  if (!tag) return 'Type a tag first.'
  if (TAG_RESERVED.some((character) => tag.includes(character))) {
    return `A tag can't contain ${TAG_RESERVED.map((c) => `"${c}"`).join(' or ')}.`
  }
  if (tag.length > TAG_MAX_LENGTH) return `A tag can be at most ${TAG_MAX_LENGTH} characters.`
  return null
}

export const tableAccessKey = (database: DatabaseId) => ['table-access', database] as const

/** A database's tables, and each role's rule: a list, or null = every table, now and later. */
export function getTableAccess(
  database: DatabaseId,
  signal?: AbortSignal,
): Promise<TableAccessInfo> {
  return call(READ_TIMEOUT_MS, signal, (s) =>
    api.GET('/table-access', {
      params: { query: database === null ? {} : { connection_id: database } },
      signal: s,
    }),
  )
}

/** Set one role's rule. `tables: null` lifts it; `[]` leaves the role no table. */
export function saveTableAccess(
  database: DatabaseId,
  role: RestrictedRole,
  tables: string[] | null,
): Promise<TableAccessInfo> {
  return call(WRITE_TIMEOUT_MS, undefined, (s) =>
    api.PUT('/table-access', {
      body: { connection_id: database, role, tables },
      signal: s,
    }),
  )
}

export function useTableAccess(database: DatabaseId) {
  return useQuery({
    queryKey: tableAccessKey(database),
    queryFn: ({ signal }) => getTableAccess(database, signal),
  })
}

/** A member's rows: tags, or null = every row. Throws ApiError row_scope_invalid (422). */
export function saveRowScope(userId: string, rowScope: string[] | null): Promise<RowScopeInfo> {
  return call(WRITE_TIMEOUT_MS, undefined, (s) =>
    api.PUT('/admin/members/{user_id}/row-scope', {
      params: { path: { user_id: userId } },
      body: { row_scope: rowScope },
      signal: s,
    }),
  )
}
