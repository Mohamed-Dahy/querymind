import { useEffect, useRef, type RefObject } from 'react'
import { useLocation, useMatches, type UIMatch } from 'react-router'

const APP_NAME = 'QueryMind'

/** The deepest matched route's page name, from its `handle` (router.tsx). */
function pageTitle(matches: UIMatch[]): string | undefined {
  for (const match of [...matches].reverse()) {
    const handle = match.handle as { title?: unknown } | undefined
    if (typeof handle?.title === 'string') return handle.title
  }
  return undefined
}

/**
 * What a full page load does for free, done by hand for in-app navigation:
 * - the browser tab says which page this is ("Evals lab · QueryMind");
 * - focus moves to the main region, so a keyboard or screen-reader user starts reading the new
 *   page instead of staying on the link they clicked (somewhere in the old page).
 * Not on the first load: there the browser starts at the top of the page as usual.
 */
export function useFocusOnRouteChange(main: RefObject<HTMLElement | null>) {
  const { pathname } = useLocation()
  const title = pageTitle(useMatches())
  // The path we last moved focus for. A ref (not "is this the first run?"), because React's
  // StrictMode runs effects twice on mount, and that second run must not count as a navigation.
  const focusedPath = useRef(pathname)

  useEffect(() => {
    document.title = title ? `${title} · ${APP_NAME}` : APP_NAME
  }, [title])

  useEffect(() => {
    if (focusedPath.current === pathname) return
    focusedPath.current = pathname
    main.current?.focus()
  }, [pathname, main])
}
