import { cn } from '@/lib/utils'

/**
 * A form's text box, select or password field: 44 px tall (a finger's size), the global focus ring,
 * and a red border when `aria-invalid`. Shared by the login page and the Settings forms.
 * Signal Lab (ADR 026): the outline brightens under the pointer and turns lime with keyboard focus.
 */
export const FIELD = cn(
  'mt-1.5 h-11 w-full rounded-lg border border-input bg-muted px-3 text-base',
  'transition-[border-color,box-shadow] duration-150 ease-out-expo hover:border-fg-muted',
  'placeholder:text-fg-muted',
  'outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger',
)

/**
 * A search box with a magnifier on its left (the explorer's table list and the glossary): 36 px, or
 * 44 px on a touch screen, and the same hover and lime focus as `FIELD`.
 */
export const SEARCH_FIELD = cn(
  'h-9 w-full rounded-lg border border-input bg-muted pr-2 pl-8 text-sm pointer-coarse:h-11',
  'transition-[border-color,box-shadow] duration-150 ease-out-expo hover:border-fg-muted',
  'placeholder:text-fg-muted',
  'outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring',
)
