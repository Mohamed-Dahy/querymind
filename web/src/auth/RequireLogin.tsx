import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'

import { useAuthStatus } from '@/api/session'
import { PageSkeleton } from '@/components/PageSkeleton'

/**
 * A page for logged-in users only (U7.1): while the page-load refresh answers, the page's shape;
 * logged out, the login page, which comes back here afterwards (`?next=`).
 */
export function RequireLogin({ children }: { children: ReactNode }) {
  const status = useAuthStatus()
  const { pathname, search } = useLocation()

  if (status === 'checking') return <PageSkeleton />
  if (status === 'out') {
    return <Navigate to={`/login?next=${encodeURIComponent(pathname + search)}`} replace />
  }
  return children
}
