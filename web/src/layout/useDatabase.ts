import { useEffect } from 'react'

import { chooseDatabase, useChosenDatabase } from '@/api/database'
import { useSession } from '@/api/session'
import type { DatabaseId } from '@/api/explorer'
import { useConnections, type ConnectionInfo } from '@/api/settings'

export type CurrentDatabase = {
  /** The database to use: a saved one's id, or null for the demo. */
  id: DatabaseId
  /** Its name, for people: "Sales", or the demo's. */
  name: string
  /** The workspace's saved databases (empty until listed, or if they can't be). */
  connections: ConnectionInfo[]
}

export const DEMO_NAME = 'Northwind + Chinook'

/**
 * The current database, checked against the workspace's databases: a choice that is no longer
 * one of them (deleted, or from before a workspace switch) falls back to the demo, and is
 * forgotten. While the list loads, the choice is trusted (the API answers 404 if it is stale).
 */
export function useDatabase(): CurrentDatabase {
  const chosen = useChosenDatabase()
  const listed = useConnections({ enabled: useSession() !== null })
  const connections = listed.data ?? []
  const found = connections.find(({ id }) => id === chosen)
  const stale = chosen !== null && listed.isSuccess && found === undefined

  useEffect(() => {
    if (stale) chooseDatabase(null)
  }, [stale])

  const id = stale ? null : chosen
  return { id, name: id === null ? DEMO_NAME : (found?.name ?? 'Your database'), connections }
}

/** An explorer path on this database: `?db=<id>` for a saved one, nothing for the demo. */
export function explorerPath(path: string, database: DatabaseId): string {
  if (database === null) return path
  const [base = '', hash] = path.split('#')
  const joiner = base.includes('?') ? '&' : '?'
  const withDb = `${base}${joiner}db=${encodeURIComponent(database)}`
  return hash === undefined ? withDb : `${withDb}#${hash}`
}
