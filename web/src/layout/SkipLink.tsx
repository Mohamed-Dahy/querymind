import type { RefObject } from 'react'

/**
 * The first thing Tab reaches: jumps over the top bar and the rail straight to the page content.
 * Hidden until focused. The click moves focus itself (the `href` alone would also change the URL),
 * and `href="#main"` still works if JavaScript is slow to start.
 */
export function SkipLink({ target }: { target: RefObject<HTMLElement | null> }) {
  return (
    <a
      href="#main"
      onClick={(event) => {
        event.preventDefault()
        target.current?.focus()
      }}
      className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2 focus:shadow-md"
    >
      Skip to content
    </a>
  )
}
