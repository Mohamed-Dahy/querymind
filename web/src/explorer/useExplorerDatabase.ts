import { useCallback, useEffect } from 'react'
import { useSearchParams } from 'react-router'

import { chooseDatabase } from '@/api/database'
import type { DatabaseId } from '@/api/explorer'
import { explorerPath } from '@/layout/useDatabase'

/**
 * The database an explorer page shows: `?db=<id>` in its URL, none = the demo (U7.1b). So a
 * shared or bookmarked table link opens the right database. The URL also sets the app's current
 * database (one for the whole app, decided with Mohamed): after opening a link about Sales, the
 * canvas asks Sales too.
 */
export function useExplorerDatabase(): DatabaseId {
  const [params] = useSearchParams()
  const database = params.get('db')
  useEffect(() => {
    chooseDatabase(database)
  }, [database])
  return database
}

/**
 * Explorer paths that stay on this page's database: `href('/explore/glossary')` →
 * `/explore/glossary?db=<id>` when the page is about a saved database.
 */
export function useExplorerHref(): (path: string) => string {
  const [params] = useSearchParams()
  const database = params.get('db')
  return useCallback((path: string) => explorerPath(path, database), [database])
}
