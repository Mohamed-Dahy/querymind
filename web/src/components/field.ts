import { cn } from '@/lib/utils'

/**
 * A form's text box, select or password field: 44 px tall (a finger's size), the global focus ring,
 * and a red border when `aria-invalid`. Shared by the login page and the Settings forms.
 */
export const FIELD = cn(
  'mt-1.5 h-11 w-full rounded-lg border border-input bg-muted px-3 text-base',
  'outline-none focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger',
)
