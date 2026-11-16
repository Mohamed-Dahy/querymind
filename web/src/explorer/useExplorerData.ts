import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { getCatalog, getSemantic, type DatabaseId } from '@/api/explorer'

import { joinData } from './data'

// The server keeps these until it restarts (or a saved database is tested again), so there's no
// need to ask again soon.
const STALE_MS = 5 * 60_000

/**
 * What the tables and terms mean. The demo's are files on the server, so this works with the
 * database down; a saved database's need it up (its dictionary is matched to its tables).
 * Keyed by database: switching never shows another database's cached answer.
 */
export function useSemanticOverview(database: DatabaseId) {
  return useQuery({
    queryKey: ['semantic', database],
    queryFn: ({ signal }) => getSemantic(database, signal),
    staleTime: STALE_MS,
  })
}

/** The tables and what they mean. Both are needed: without the meaning, a PII badge would be wrong. */
export function useExplorerData(database: DatabaseId) {
  const catalog = useQuery({
    queryKey: ['catalog', database],
    queryFn: ({ signal }) => getCatalog(database, signal),
    staleTime: STALE_MS,
    // A failure has already waited out the server's 10 s connect timeout. Trying again only makes
    // the message later, and the page has a Try again button.
    retry: false,
  })
  const semantic = useSemanticOverview(database)
  const data = useMemo(
    () => (catalog.data && semantic.data ? joinData(catalog.data, semantic.data) : undefined),
    [catalog.data, semantic.data],
  )
  return {
    data,
    catalog: catalog.data,
    semantic: semantic.data,
    error: catalog.error ?? semantic.error,
    pending: catalog.isPending || semantic.isPending,
    retry: () => {
      if (catalog.isError) void catalog.refetch()
      if (semantic.isError) void semantic.refetch()
    },
  }
}
