import { LoaderCircle } from 'lucide-react'

/**
 * What a click on a button that opens a panel (the drawer, the history sheet, the command palette)
 * shows while that panel's code downloads, so the click never looks ignored. A small pill at the
 * bottom of the screen; it takes no focus, and the panel replaces it.
 */
export function OverlayLoading() {
  return (
    <div
      role="status"
      className="fixed bottom-4 left-1/2 z-50 flex -translate-x-1/2 items-center gap-2 rounded-full border bg-surface px-4 py-2 text-sm shadow-lg"
    >
      <LoaderCircle aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
      Opening…
    </div>
  )
}
