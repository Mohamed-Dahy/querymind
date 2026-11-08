import { useQueryClient } from '@tanstack/react-query'
import { LogIn, LogOut } from 'lucide-react'
import { Link, useNavigate } from 'react-router'

import { logOut } from '@/api/auth'
import { useSession } from '@/api/session'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

const ROLE_NAMES = { admin: 'Admin', analyst: 'Analyst', viewer: 'Viewer' } as const

/** Who is logged in, their role, and "Log out"; or "Log in" on the public pages (U7.1). */
export function UserMenu() {
  const session = useSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

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
      {/* The role in words, never colour alone. From 640 px: a phone's top bar has no room for it. */}
      <Badge variant="outline" className="hidden sm:inline-flex">
        <span className="sr-only">Role: </span>
        {ROLE_NAMES[role]}
      </Badge>
      <Button
        type="button"
        variant="outline"
        className="pointer-coarse:min-w-11"
        onClick={() => {
          void logOut().then(() => {
            // Nothing of this user stays in memory for whoever logs in next.
            queryClient.clear()
            void navigate('/login', { replace: true })
          })
        }}
      >
        <LogOut aria-hidden="true" />
        <span className="sr-only sm:not-sr-only">Log out</span>
      </Button>
    </div>
  )
}
