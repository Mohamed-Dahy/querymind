import { useMemo } from 'react'

import { useTheme } from './theme'

// Our tokens for the series, in order (src/styles/tokens.css): never a colour picked by hand.
const SERIES_TOKENS = ['--signal', '--info', '--warn'] as const

export type Colours = {
  series: string[]
  text: string
  muted: string
  grid: string
  surface: string
}

/**
 * The token colours as real values. SVG attributes like fill="…" can't read CSS variables, so we
 * read them from the page, again whenever the theme changes.
 */
export function useTokenColours(): Colours {
  const { theme } = useTheme()
  return useMemo(() => {
    // `theme` is here so the colours are read again after a theme switch.
    void theme
    const style = getComputedStyle(document.documentElement)
    const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
    return {
      series: SERIES_TOKENS.map((name) => read(name, 'currentColor')),
      text: read('--fg', 'currentColor'),
      muted: read('--fg-muted', 'currentColor'),
      grid: read('--input', 'currentColor'),
      surface: read('--surface', 'Canvas'),
    }
  }, [theme])
}
