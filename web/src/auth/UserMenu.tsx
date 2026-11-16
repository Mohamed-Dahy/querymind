import { useQueryClient } from '@tanstack/react-query'
import { ChevronDown, CircleUser, LogIn, LogOut, Settings } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router'

import { logOut } from '@/api/auth'
import { useSession } from '@/api/session'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const ROLE_NAMES = { admin: 'Admin', analyst: 'Analyst', viewer: 'Viewer' } as const

const ITEM = cn(
  'flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-left text-sm transition-colors',
  'hover:bg-muted [&_svg]:size-4 [&_svg]:shrink-0',
)

/**
 * Who is logged in and their role, then the account menu: Settings and Log out (U7.1b; the
 * workspace switcher joins it in U7.1d). One button, so a phone's top bar keeps a single row.
 * Logged out (on the public evals lab): a "Log in" link.
 */
export function UserMenu() {
  const session = useSession()

  if (!session) {
    return (
      <Button asChild variant="outline" className="pointer-coarse:min-w-11">
        <Link to="/login">
          <LogIn aria-hidden="true" />
          Log in
        </Link>
      </Button>
    )
  }

  const { email, role, workspace } = session.user
  return (
    <div className="flex items-center gap-2">
      <p className="hidden text-sm text-fg-muted lg:block" title={workspace.name}>
        <span className="sr-only">Logged in as </span>
        {email}
      </p>
      {/* The role in words, never colour alone. From 640 px: a phone's top bar has no room for it
          (the account menu says it there). */}
      <Badge variant="outline" className="hidden sm:inline-flex">
        <span className="sr-only">Role: </span>
        {ROLE_NAMES[role]}
      </Badge>
      <AccountMenu email={email} role={ROLE_NAMES[role]} workspace={workspace.name} />
    </div>
  )
}

/**
 * A disclosure, not an ARIA menu: a button that shows a small panel with a link and a button, which
 * keep their own roles and the usual Tab order (WAI's pattern for navigation). Radix's menu would
 * add ~28 kB to every page load for two items. It closes on Escape (focus back on the button), a
 * click outside, Tab leaving it, or choosing an item.
 */
function AccountMenu({
  email,
  role,
  workspace,
}: {
  email: string
  role: string
  workspace: string
}) {
  const [open, setOpen] = useState(false)
  const box = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  useEffect(() => {
    if (!open) return undefined
    function onPointerDown(event: PointerEvent) {
      if (!box.current?.contains(event.target as Node)) setOpen(false)
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== 'Escape') return
      setOpen(false)
      button.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  return (
    <div
      ref={box}
      className="relative"
      onBlur={(event) => {
        // Tab moved focus out of the button and the panel: nothing left to point at.
        if (open && !box.current?.contains(event.relatedTarget as Node | null)) setOpen(false)
      }}
    >
      <Button
        ref={button}
        type="button"
        variant="outline"
        aria-expanded={open}
        aria-controls={panelId}
        className="pointer-coarse:min-w-11"
        onClick={() => setOpen((was) => !was)}
      >
        <CircleUser aria-hidden="true" />
        <span className="sr-only">Account</span>
        <ChevronDown aria-hidden="true" className="hidden sm:block" />
      </Button>
      {open && (
        <div
          id={panelId}
          className="absolute top-full right-0 z-50 mt-1 w-64 rounded-lg bg-popover p-1 text-popover-foreground shadow-md ring-1 ring-foreground/10"
        >
          <div className="px-2 py-1.5 text-sm">
            <p className="truncate font-medium">{email}</p>
            <p className="truncate text-xs text-fg-muted">
              {role} · {workspace}
            </p>
          </div>
          <div aria-hidden="true" className="-mx-1 my-1 h-px bg-border" />
          <ul>
            <li>
              {/* The workspace's key, databases, members… */}
              <Link to="/settings" className={ITEM} onClick={() => setOpen(false)}>
                <Settings aria-hidden="true" />
                Settings
              </Link>
            </li>
            <li>
              <button
                type="button"
                className={ITEM}
                onClick={() => {
                  setOpen(false)
                  void logOut().then(() => {
                    // Nothing of this user stays in memory for whoever logs in next.
                    queryClient.clear()
                    void navigate('/login', { replace: true })
                  })
                }}
              >
                <LogOut aria-hidden="true" />
                Log out
              </button>
            </li>
          </ul>
        </div>
      )}
    </div>
  )
}
