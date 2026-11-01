import {
  Database,
  Gauge,
  LayoutGrid,
  Network,
  ScanSearch,
  SquarePen,
  type LucideIcon,
} from 'lucide-react'
import { useRef } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

import { CanvasProvider } from '@/canvas/CanvasProvider'
import { useNewConversation } from '@/canvas/useConversation'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { ApiStatus } from './ApiStatus'
import { SkipLink } from './SkipLink'
import { useFocusOnRouteChange } from './useFocusOnRouteChange'

type NavItem = { to: string; label: string; Icon: LucideIcon; matches: (path: string) => boolean }

const NAV_ITEMS: NavItem[] = [
  // A conversation (/c/…) is still the canvas.
  { to: '/', label: 'Canvas', Icon: LayoutGrid, matches: (p) => p === '/' || p.startsWith('/c/') },
  { to: '/explore', label: 'Explore', Icon: Network, matches: (p) => p.startsWith('/explore') },
  { to: '/evals', label: 'Evals', Icon: Gauge, matches: (p) => p.startsWith('/evals') },
]

/** The frame around every page: top bar, left rail, and the page itself in <main>. */
export function AppShell() {
  const main = useRef<HTMLElement>(null)
  useFocusOnRouteChange(main)

  return (
    // Above the routes, so the conversations survive `/` → `/c/<id>` and a visit to Explore; and
    // above the top bar, for its "New conversation" button.
    <CanvasProvider>
      <div className="flex min-h-dvh flex-col">
        <SkipLink target={main} />

        <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-surface px-4 py-2">
          <Link
            to="/"
            className="flex min-h-11 items-center gap-2 rounded-lg font-heading text-lg font-semibold"
          >
            <ScanSearch aria-hidden="true" className="size-5 text-signal" />
            QueryMind
          </Link>
          <span className="flex items-center gap-1.5 text-sm text-fg-muted">
            <Database aria-hidden="true" className="size-4" />
            Northwind + Chinook
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2">
            <NewConversationButton />
            <ApiStatus />
            <ThemeSwitcher />
          </div>
        </header>

        <div className="flex flex-1 flex-col md:flex-row">
          <NavRail />
          {/* tabIndex -1: focusable by code (route change, skip link) but not a Tab stop itself. */}
          <main id="main" ref={main} tabIndex={-1} className="canvas-grid flex-1 outline-none">
            <Outlet />
          </main>
        </div>
      </div>
    </CanvasProvider>
  )
}

function NewConversationButton() {
  const newConversation = useNewConversation()
  return (
    <Button type="button" variant="outline" onClick={newConversation}>
      <SquarePen aria-hidden="true" />
      New conversation
    </Button>
  )
}

/** The left rail (a row on phones): the three areas, then the history (filled in by U3). */
function NavRail() {
  const { pathname } = useLocation()

  return (
    <nav
      aria-label="Main"
      className="border-b bg-surface md:w-56 md:shrink-0 md:border-r md:border-b-0"
    >
      <ul className="flex gap-1 p-2 md:flex-col">
        {NAV_ITEMS.map(({ to, label, Icon, matches }) => {
          const current = matches(pathname)
          return (
            <li key={to}>
              <Link
                to={to}
                // Tells screen readers which page this is; the look follows it (not colour alone: weight too).
                aria-current={current ? 'page' : undefined}
                className={cn(
                  'flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm transition-colors',
                  current
                    ? 'bg-muted font-medium text-fg'
                    : 'text-fg-muted hover:bg-muted hover:text-fg',
                )}
              >
                <Icon aria-hidden="true" className="size-4" />
                {label}
              </Link>
            </li>
          )
        })}
      </ul>

      <section aria-labelledby="history-heading" className="hidden px-5 py-3 md:block">
        <h2 id="history-heading" className="text-xs tracking-wide text-fg-muted uppercase">
          History
        </h2>
        <p className="mt-2 text-sm text-fg-muted">Your past conversations will be listed here.</p>
      </section>

      {import.meta.env.DEV && (
        <p className="hidden px-5 pb-3 md:block">
          <Link to="/styleguide" className="text-sm text-info underline underline-offset-4">
            Style guide (dev)
          </Link>
        </p>
      )}
    </nav>
  )
}
