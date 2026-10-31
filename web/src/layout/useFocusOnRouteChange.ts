import { useEffect, useRef, type RefObject } from 'react'
import { useLocation, useMatches, useNavigationType, type UIMatch } from 'react-router'

const APP_NAME = 'QueryMind'

/**
 * Navigation state for a URL change the user didn't ask for, e.g. the canvas putting a new
 * conversation's id in the URL: focus stays where it is (in the composer).
 * `navigate(to, { replace: true, state: KEEP_FOCUS })`
 */
export const KEEP_FOCUS = { keepFocus: true } as const

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
 * Not on the first load: there the browser starts at the top of the page as usual. Not for a
 * replace with KEEP_FOCUS either; Back/Forward to that entry later do move focus.
 */
export function useFocusOnRouteChange(main: RefObject<HTMLElement | null>) {
  const { pathname, state } = useLocation()
  const keepFocus =
    useNavigationType() === 'REPLACE' && (state as typeof KEEP_FOCUS | null)?.keepFocus === true
  const title = pageTitle(useMatches())
  // The path we last moved focus for. A ref (not "is this the first run?"), because React's
  // StrictMode runs effects twice on mount, and that second run must not count as a navigation.
  const focusedPath = useRef(pathname)

  useEffect(() => {
    document.title = title ? `${title} · ${APP_NAME}` : APP_NAME
  }, [title])

  useEffect(() => {
    if (focusedPath.current === pathname) return
    // Remember the path even when focus stays, so the next render doesn't count it again.
    focusedPath.current = pathname
    if (!keepFocus) main.current?.focus()
  }, [pathname, keepFocus, main])
}
