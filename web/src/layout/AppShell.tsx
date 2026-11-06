import {
  Database,
  Gauge,
  LayoutGrid,
  Network,
  ScanSearch,
  History,
  Search,
  SquarePen,
  type LucideIcon,
} from 'lucide-react'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { Link, Outlet, useLocation } from 'react-router'

import { CanvasProvider } from '@/canvas/CanvasProvider'
import { useNewConversation } from '@/canvas/useConversation'
import { Kbd } from '@/components/Kbd'
import { OverlayLoading } from '@/components/OverlayLoading'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { isPaletteShortcut, MODIFIER } from '@/lib/keys'
import { cn } from '@/lib/utils'

import { ApiStatus } from './ApiStatus'
import { HistoryRail } from './HistoryRail'
import { SkipLink } from './SkipLink'
import { useDeleteWithUndo, type DeleteWithUndo } from './useDeleteWithUndo'
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

// Loaded the first time it opens: most visits never use it.
const CommandPalette = lazy(() => import('./CommandPalette'))

/** The frame around every page: top bar, left rail, and the page itself in <main>. */
export function AppShell() {
  return (
    // Above the routes, so the conversations survive `/` → `/c/<id>` and a visit to Explore; and
    // above the top bar, for its "New conversation" button.
    <CanvasProvider>
      <Frame />
    </CanvasProvider>
  )
}

function Frame() {
  const main = useRef<HTMLElement>(null)
  useFocusOnRouteChange(main)
  // Here, because this stays mounted: a delete waiting out its Undo must survive the phone's sheet
  // closing, and the palette must not list a conversation that is about to go.
  const deletes = useDeleteWithUndo()
  const palette = usePalette()

  return (
    <>
      <div className="flex min-h-dvh flex-col">
        <SkipLink target={main} />

        {/* On a phone: two short rows (logo and the two actions; status and theme), with the
            buttons' words kept for screen readers only. From 640 px: the words come back. */}
        <header className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b bg-surface px-4 py-2 sm:gap-y-2">
          <Link
            to="/"
            className="flex min-h-11 items-center gap-2 rounded-lg font-heading text-lg font-semibold"
          >
            <ScanSearch aria-hidden="true" className="size-5 text-signal" />
            QueryMind
          </Link>
          {/* Not on a phone: the canvas's own intro names both businesses. */}
          <span className="hidden items-center gap-1.5 text-sm text-fg-muted sm:flex">
            <Database aria-hidden="true" className="size-4" />
            Northwind + Chinook
          </span>
          <div className="ml-auto flex items-center gap-2 sm:gap-4">
            <Button
              type="button"
              variant="outline"
              aria-haspopup="dialog"
              aria-keyshortcuts="Control+K Meta+K"
              className="pointer-coarse:min-w-11"
              onClick={(event) => palette.show(event.currentTarget)}
            >
              <Search aria-hidden="true" />
              <span className="sr-only sm:not-sr-only">Search</span>
              {/* Not read out: aria-keyshortcuts says it. Hidden on phones, which have no Ctrl key. */}
              <span aria-hidden="true" className="hidden sm:inline">
                <Kbd>{`${MODIFIER} K`}</Kbd>
              </span>
            </Button>
            <NewConversationButton />
          </div>
          <div className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-2 sm:w-auto">
            <ApiStatus />
            <ThemeSwitcher compact />
          </div>
        </header>

        <div className="flex flex-1 flex-col md:flex-row">
          <NavRail deletes={deletes} />
          {/* tabIndex -1: focusable by code (route change, skip link) but not a Tab stop itself.
              min-w-0: a flex item won't shrink below its content's width by default, so one wide
              table pushed the whole page sideways beside the rail (768 and 1024 px). */}
          <main
            id="main"
            ref={main}
            tabIndex={-1}
            className="canvas-grid min-w-0 flex-1 outline-none"
          >
            <Outlet />
          </main>
        </div>
      </div>

      {palette.loaded && (
        <Suspense fallback={<OverlayLoading />}>
          <CommandPalette
            open={palette.open}
            onOpenChange={palette.setOpen}
            opener={palette.opener}
            hidden={deletes.hidden}
          />
        </Suspense>
      )}
    </>
  )
}

function NewConversationButton() {
  const newConversation = useNewConversation()
  return (
    <Button
      type="button"
      variant="outline"
      className="pointer-coarse:min-w-11"
      onClick={newConversation}
    >
      <SquarePen aria-hidden="true" />
      {/* A phone shows "New"; the name stays "New conversation", which starts with what is seen
          (so voice control's "click New" works). */}
      <span aria-hidden="true" className="sm:hidden">
        New
      </span>
      <span className="sr-only sm:not-sr-only">New conversation</span>
    </Button>
  )
}

/** Ctrl+K / ⌘K anywhere opens the palette (and closes it again); `show` is for the button. */
function usePalette() {
  const [open, setOpenState] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const opener = useRef<HTMLElement | null>(null)
  // The same as `open`, for the key listener, which is added once and would see an old `open`.
  const isOpen = useRef(false)

  const setOpen = useCallback((next: boolean) => {
    isOpen.current = next
    setOpenState(next)
  }, [])
  const show = useCallback(
    (from: Element | null) => {
      opener.current = from instanceof HTMLElement ? from : null
      setLoaded(true)
      setOpen(true)
    },
    [setOpen],
  )

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!isPaletteShortcut(event)) return
      // Not the browser's own Ctrl+K (its search box): this page has a use for it.
      event.preventDefault()
      if (isOpen.current) setOpen(false)
      else show(document.activeElement)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [setOpen, show])

  return { open, loaded, opener, setOpen, show }
}

/** The left rail (a row on phones): the three areas, then the history. */
function NavRail({ deletes }: { deletes: DeleteWithUndo }) {
  const { pathname } = useLocation()
  const [sheetOpen, setSheetOpen] = useState(false)
  const [sheetLoaded, setSheetLoaded] = useState(false)
  const historyButton = useRef<HTMLButtonElement>(null)

  return (
    <nav
      aria-label="Main"
      className="border-b bg-surface md:w-56 md:shrink-0 md:border-r md:border-b-0"
    >
      <ul className="flex flex-wrap gap-0.5 p-2 sm:gap-1 md:flex-col md:flex-nowrap">
        {NAV_ITEMS.map(({ to, label, Icon, matches }) => {
          const current = matches(pathname)
          return (
            <li key={to}>
              <Link
                to={to}
                // Tells screen readers which page this is; the look follows it (not colour alone: weight too).
                aria-current={current ? 'page' : undefined}
                className={cn(
                  'flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm sm:px-3 transition-colors',
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
            className="flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm sm:px-3 text-fg-muted transition-colors hover:bg-muted hover:text-fg"
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
        <Suspense fallback={<OverlayLoading />}>
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
