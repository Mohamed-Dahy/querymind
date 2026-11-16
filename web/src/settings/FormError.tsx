import { TriangleAlert } from 'lucide-react'

import type { ApiError } from '@/api/client'
import { errorTitle } from '@/api/errors'

/**
 * Why a Settings action failed: our words (the title) and the API's, which name the fix ("Run:
 * REVOKE CREATE …"). `role="alert"`, so a screen reader hears it at once; give it an `id` and
 * point the fields at it with `aria-describedby`.
 */
export function FormError({ id, error }: { id?: string; error: ApiError }) {
  return (
    <div
      id={id}
      role="alert"
      className="flex items-start gap-1.5 rounded-lg border border-danger px-3 py-2 text-sm"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
      <div className="min-w-0">
        <p className="font-medium">{errorTitle(error.code)}</p>
        <p className="break-words text-fg-muted">{error.message}</p>
      </div>
    </div>
  )
}
