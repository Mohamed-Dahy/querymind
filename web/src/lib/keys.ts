// Keyboard shortcuts shared by the composer and the command palette.

// Only the hint text differs: both Ctrl and ⌘ work on every system.
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform)
export const MODIFIER = IS_MAC ? '⌘' : 'Ctrl'

/** Ctrl+K or ⌘K, and nothing else held: opens (or closes) the command palette. */
export function isPaletteShortcut(event: KeyboardEvent): boolean {
  return (
    event.key.toLowerCase() === 'k' &&
    (event.ctrlKey || event.metaKey) &&
    !event.altKey &&
    !event.shiftKey
  )
}
