import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

import { getCatalog, getSemantic } from '@/api/explorer'

import { joinData } from './data'

// The server keeps these until it restarts, so there's no need to ask again soon.
const STALE_MS = 5 * 60_000

/** What the tables and terms mean. Files on the server, so this works with the database down. */
export function useSemanticOverview() {
  return useQuery({
    queryKey: ['semantic'],
    queryFn: ({ signal }) => getSemantic(signal),
    staleTime: STALE_MS,
  })
}

/** The tables and what they mean. Both are needed: without the meaning, a PII badge would be wrong. */
export function useExplorerData() {
  const catalog = useQuery({
    queryKey: ['catalog'],
    queryFn: ({ signal }) => getCatalog(signal),
    staleTime: STALE_MS,
  })
  const semantic = useSemanticOverview()
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
