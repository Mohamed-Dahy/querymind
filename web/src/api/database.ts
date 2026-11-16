/**
 * Which database the app is about right now (U7.1b): one of the workspace's saved databases, or
 * null for the demo. One choice for the whole app: the canvas asks it, the explorer shows it, and
 * the switcher, an explorer link's `?db=` or an opened conversation change it.
 *
 * Kept per workspace (a database id means nothing in another workspace), in memory and in
 * localStorage so a reload keeps it. This is the raw choice: `useDatabase()` (layout/useDatabase.ts)
 * checks it against the workspace's databases, so a deleted one falls back to the demo.
 */
import { useSyncExternalStore } from 'react'

import type { DatabaseId } from './explorer'
import { getSession, useSession } from './session'

const STORAGE_PREFIX = 'querymind-database:'

// The choice per workspace on this page load: the source of truth while it runs. localStorage only
// carries it over a reload, and may be unavailable (a private window).
const chosen = new Map<string, DatabaseId>()
const listeners = new Set<() => void>()

function readChoice(workspaceId: string | undefined): DatabaseId {
  if (workspaceId === undefined) return null
  if (!chosen.has(workspaceId)) {
    let stored: string | null = null
    try {
      stored = localStorage.getItem(STORAGE_PREFIX + workspaceId)
    } catch {
      // No storage: the demo until something is chosen.
    }
    chosen.set(workspaceId, stored)
  }
  return chosen.get(workspaceId) ?? null
}

/** The current choice for the logged-in workspace, outside React (e.g. when a question is sent). */
export function getChosenDatabase(): DatabaseId {
  return readChoice(getSession()?.user.workspace.id)
}

/** Choose a database (null = the demo) for the logged-in workspace. Nothing happens logged out. */
export function chooseDatabase(database: DatabaseId) {
  const workspaceId = getSession()?.user.workspace.id
  if (workspaceId === undefined || readChoice(workspaceId) === database) return
  chosen.set(workspaceId, database)
  try {
    if (database === null) localStorage.removeItem(STORAGE_PREFIX + workspaceId)
    else localStorage.setItem(STORAGE_PREFIX + workspaceId, database)
  } catch {
    // Not kept over a reload; still chosen for this page load.
  }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

/** The raw choice, re-rendering when it (or the workspace) changes. */
export function useChosenDatabase(): DatabaseId {
  const workspaceId = useSession()?.user.workspace.id
  return useSyncExternalStore(subscribe, () => readChoice(workspaceId))
}

/** Tests: forget every choice made in memory (localStorage is cleared by the test setup). */
export function resetChosenDatabases() {
  chosen.clear()
  for (const listener of listeners) listener()
}
