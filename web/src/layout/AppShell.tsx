import {
  Database,
  Gauge,
  LayoutGrid,
  Network,
  ScanSearch,
  History,
  SquarePen,
  type LucideIcon,
} from 'lucide-react'
import { lazy, Suspense, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

import { CanvasProvider } from '@/canvas/CanvasProvider'
import { useNewConversation } from '@/canvas/useConversation'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { ApiStatus } from './ApiStatus'
import { HistoryRail } from './HistoryRail'
import { SkipLink } from './SkipLink'
import { useDeleteWithUndo } from './useDeleteWithUndo'
import { useFocusOnRouteChange } from './useFocusOnRouteChange'

type NavItem = { to: string; label: string; Icon: LucideIcon; matches: (path: string) => boolean }

const NAV_ITEMS: NavItem[] = [
  // A conversation (/c/…) is still the canvas.
  { to: '/', label: 'Canvas', Icon: LayoutGrid, matches: (p) => p === '/' || p.startsWith('/c/') },
  { to: '/explore', label: 'Explore', Icon: Network, matches: (p) => p.startsWith('/explore') },
  { to: '/evals', label: 'Evals', Icon: Gauge, matches: (p) => p.startsWith('/evals') },
]

// Only phones open it, so only they download it (and the dialog code it needs).
const HistorySheet = lazy(() =>
  import('./HistorySheet').then(({ HistorySheet }) => ({ default: HistorySheet })),
)

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

/** The left rail (a row on phones): the three areas, then the history. */
function NavRail() {
  const { pathname } = useLocation()
  // Here, because this stays mounted: a delete waiting out its Undo must survive the sheet closing.
  const deletes = useDeleteWithUndo()
  const [sheetOpen, setSheetOpen] = useState(false)
  const [sheetLoaded, setSheetLoaded] = useState(false)
  const historyButton = useRef<HTMLButtonElement>(null)

  return (
    <nav
      aria-label="Main"
      className="border-b bg-surface md:w-56 md:shrink-0 md:border-r md:border-b-0"
    >
      <ul className="flex flex-wrap gap-1 p-2 md:flex-col md:flex-nowrap">
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
        <li className="ml-auto md:hidden">
          <button
            ref={historyButton}
            type="button"
            aria-haspopup="dialog"
            onClick={() => {
              setSheetLoaded(true)
              setSheetOpen(true)
            }}
            className="flex min-h-11 items-center gap-2 rounded-lg px-3 text-sm text-fg-muted transition-colors hover:bg-muted hover:text-fg"
          >
            <History aria-hidden="true" className="size-4" />
            History
          </button>
        </li>
      </ul>

      <section aria-labelledby="history-heading" className="hidden px-3 py-3 md:block">
        <h2 id="history-heading" className="px-2 text-xs tracking-wide text-fg-muted uppercase">
          History
        </h2>
        <HistoryRail deletes={deletes} />
      </section>

      {sheetLoaded && (
        <Suspense fallback={null}>
          <HistorySheet
            open={sheetOpen}
            onOpenChange={setSheetOpen}
            deletes={deletes}
            opener={historyButton}
          />
        </Suspense>
      )}

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
