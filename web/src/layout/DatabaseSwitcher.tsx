import { Database } from 'lucide-react'
import { useId } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { chooseDatabase } from '@/api/database'
import { useNewConversation } from '@/canvas/useConversation'
import { cn } from '@/lib/utils'

import { DEMO_NAME, explorerPath, useDatabase } from './useDatabase'

// The select's value for the demo (an <option> needs a string; the API's is null).
const DEMO = 'demo'

type DatabaseSwitcherProps = {
  /**
   * `bar`: in the top bar, from 640 px. `page`: at the top of the canvas and explorer on a phone,
   * where the top bar has no room left (U6.3's height budget).
   */
  place: 'bar' | 'page'
}

/**
 * Which database the app is about (U7.1b): the demo, or one of the workspace's own. One choice for
 * the whole app. Changing it on the canvas starts a new conversation (a conversation stays on its
 * database); on the explorer it shows the same page for the new database. With no database of its
 * own, the workspace just sees the demo's name, as before.
 */
export function DatabaseSwitcher({ place }: DatabaseSwitcherProps) {
  const { id, connections } = useDatabase()
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const newConversation = useNewConversation()
  const selectId = useId()
  const visibility = place === 'bar' ? 'hidden sm:flex' : 'flex sm:hidden'

  if (connections.length === 0) {
    // Nothing to choose. On a phone the canvas's own intro names the demo, as before.
    if (place === 'page') return null
    return (
      <span className={cn('items-center gap-1.5 text-sm text-fg-muted', visibility)}>
        <Database aria-hidden="true" className="size-4" />
        {DEMO_NAME}
      </span>
    )
  }

  function choose(value: string) {
    const next = value === DEMO ? null : value
    chooseDatabase(next)
    if (pathname.startsWith('/explore')) {
      // A table page may not exist on the other database: its list instead.
      const page = pathname.startsWith('/explore/glossary') ? '/explore/glossary' : '/explore'
      void navigate(explorerPath(page, next))
    } else if (pathname === '/' || pathname.startsWith('/c/')) {
      newConversation()
    }
  }

  return (
    <div className={cn('min-w-0 items-center gap-1.5', visibility)}>
      <Database aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
      <label htmlFor={selectId} className={place === 'bar' ? 'sr-only' : 'text-sm font-medium'}>
        Database
      </label>
      <select
        id={selectId}
        value={id ?? DEMO}
        onChange={(event) => choose(event.target.value)}
        className="h-8 max-w-56 min-w-0 truncate rounded-lg border border-input bg-muted px-2.5 text-sm font-medium transition-colors outline-none hover:border-fg-muted focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:h-11"
      >
        <option value={DEMO}>Demo: {DEMO_NAME}</option>
        {connections.map((connection) => (
          <option key={connection.id} value={connection.id}>
            {connection.name}
          </option>
        ))}
      </select>
    </div>
  )
}
