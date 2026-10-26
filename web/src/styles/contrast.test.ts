import { describe, expect, it } from 'vitest'

import tokensCss from './tokens.css?raw'

// WCAG 2.2 contrast, checked on the token file itself, so a colour change that hurts readability fails
// `npm test` (and CI) instead of reaching the page. Formula: https://www.w3.org/TR/WCAG22/#dfn-contrast-ratio

type Theme = 'dark' | 'light'

const BACKGROUNDS = ['--bg', '--surface', '--muted']
// Every token used as text (or as an icon next to text) must be readable on every background.
const TEXT = ['--fg', '--fg-muted', '--signal', '--warn', '--danger', '--info']
// Control outlines and the focus ring are UI parts: 3:1 against what is next to them (WCAG 1.4.11).
const UI_PARTS = ['--input', '--ring']
const UI_BACKGROUNDS = ['--bg', '--surface']

/** The hex tokens of each theme block in tokens.css (rgb(...) tokens like --border are decorative). */
function readTokens(css: string): Record<Theme, Record<string, string>> {
  const themes: Record<Theme, Record<string, string>> = { dark: {}, light: {} }
  for (const block of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selector = block[1] ?? ''
    const theme: Theme | null = selector.includes("'light'")
      ? 'light'
      : selector.includes("'dark'")
        ? 'dark'
        : null
    if (theme === null) continue
    for (const decl of (block[2] ?? '').matchAll(/(--[\w-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
      themes[theme][decl[1] ?? ''] = decl[2] ?? ''
    }
  }
  return themes
}

/** Relative luminance of a #rrggbb colour (WCAG definition). */
function luminance(hex: string): number {
  const channels = [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16) / 255)
  const [r = 0, g = 0, b = 0] = channels.map((c) =>
    c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  )
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

/** Contrast ratio between two colours: 1 (same) to 21 (black on white). */
function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return ((light ?? 0) + 0.05) / ((dark ?? 0) + 0.05)
}

const tokens = readTokens(tokensCss)

describe('contrast formula', () => {
  it('matches the WCAG reference values', () => {
    expect(contrast('#000000', '#ffffff')).toBeCloseTo(21, 5)
    expect(contrast('#777777', '#ffffff')).toBeCloseTo(4.48, 2)
    expect(contrast('#ffffff', '#ffffff')).toBe(1)
  })
})

describe.each(['dark', 'light'] as const)('%s theme tokens', (theme) => {
  const t = tokens[theme]

  it('defines every checked token as a hex colour', () => {
    for (const name of [...BACKGROUNDS, ...TEXT, ...UI_PARTS, '--on-signal']) {
      expect(t[name], `${theme} ${name}`).toMatch(/^#[0-9a-fA-F]{6}$/)
    }
  })

  it.each(TEXT.flatMap((text) => BACKGROUNDS.map((bg) => [text, bg])))(
    'text %s on %s is at least 4.5:1',
    (text, bg) => {
      expect(contrast(t[text] ?? '', t[bg] ?? '')).toBeGreaterThanOrEqual(4.5)
    },
  )

  it('text on a --signal fill (primary button) is at least 4.5:1', () => {
    expect(contrast(t['--on-signal'] ?? '', t['--signal'] ?? '')).toBeGreaterThanOrEqual(4.5)
  })

  it.each(UI_PARTS.flatMap((part) => UI_BACKGROUNDS.map((bg) => [part, bg])))(
    'UI part %s on %s is at least 3:1',
    (part, bg) => {
      expect(contrast(t[part] ?? '', t[bg] ?? '')).toBeGreaterThanOrEqual(3)
    },
  )
})
