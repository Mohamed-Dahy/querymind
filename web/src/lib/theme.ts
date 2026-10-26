import { useSyncExternalStore } from 'react'

// The person's choice, and the theme it resolves to ("system" follows the OS setting).
export type ThemeChoice = 'dark' | 'light' | 'system'
export type Theme = 'dark' | 'light'

// index.html reads the same key before the first paint (keep the two in sync).
export const THEME_STORAGE_KEY = 'querymind-theme'
const LIGHT_QUERY = '(prefers-color-scheme: light)'

const listeners = new Set<() => void>()
let currentChoice: ThemeChoice | null = null

/** The saved choice, or "system". Storage can be blocked (private mode, settings), so it never throws. */
export function readThemeChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(THEME_STORAGE_KEY)
    if (saved === 'dark' || saved === 'light' || saved === 'system') return saved
  } catch {
    // Storage unavailable: fall back to the OS setting.
  }
  return 'system'
}

/** "system" becomes the OS's current setting; dark when the OS has no preference. */
export function resolveTheme(choice: ThemeChoice): Theme {
  if (choice !== 'system') return choice
  return window.matchMedia(LIGHT_QUERY).matches ? 'light' : 'dark'
}

function getChoice(): ThemeChoice {
  currentChoice ??= readThemeChoice()
  return currentChoice
}

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
}

/** Save and apply a new choice; every component using `useTheme` re-renders. */
export function setThemeChoice(choice: ThemeChoice): void {
  currentChoice = choice
  try {
    localStorage.setItem(THEME_STORAGE_KEY, choice)
  } catch {
    // Not saved, but still applied for this visit.
  }
  applyTheme(resolveTheme(choice))
  listeners.forEach((listener) => listener())
}

/** Call once at startup: apply the theme, and follow the OS when the choice is "system". */
export function initTheme(): void {
  applyTheme(resolveTheme(getChoice()))
  window.matchMedia(LIGHT_QUERY).addEventListener('change', () => {
    if (getChoice() === 'system') applyTheme(resolveTheme('system'))
  })
}

// useSyncExternalStore: React's way to read a value that lives outside React (here: module state + the
// OS setting). It re-renders the component whenever `subscribe` reports a change.
function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(LIGHT_QUERY)
  listeners.add(onChange)
  media.addEventListener('change', onChange)
  return () => {
    listeners.delete(onChange)
    media.removeEventListener('change', onChange)
  }
}

/** The current choice and resolved theme, plus a setter. Shared by every component that uses it. */
export function useTheme(): {
  choice: ThemeChoice
  theme: Theme
  setChoice: typeof setThemeChoice
} {
  const choice = useSyncExternalStore(subscribe, getChoice)
  const theme = useSyncExternalStore(subscribe, () => resolveTheme(getChoice()))
  return { choice, theme, setChoice: setThemeChoice }
}
