import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { sessionEnded, useAuthStatus } from '@/api/session'
import { PageSkeleton } from '@/components/PageSkeleton'

/**
 * A page for logged-in users only (U7.1): while the page-load refresh answers, the page's shape;
 * logged out, the login page, which comes back here afterwards (`?next=`). A visitor who simply opens
 * the site (`/`, nothing more) sees the front page instead (U8.4): a login form says nothing about
 * what QueryMind is. Any deeper link still goes straight to the login.
 */
export function RequireLogin({ children }: { children: ReactNode }) {
  const status = useAuthStatus()
  const { pathname, search } = useLocation()

  if (status === 'checking') return <PageSkeleton />
  if (status === 'out') {
    // Not after a session ended on its own (expired, revoked): the login says why, and brings them back.
    if (pathname === '/' && search === '' && !sessionEnded()) {
      return <Navigate to="/welcome" replace />
    }
    return <Navigate to={`/login?next=${encodeURIComponent(pathname + search)}`} replace />
  }
  return children
}
