import { afterEach, describe, expect, it, vi } from 'vitest'

import { readThemeChoice, resolveTheme, setThemeChoice, THEME_STORAGE_KEY } from './theme'

/** Pretend the OS prefers light (true) or dark (false) for this test. */
function osPrefersLight(light: boolean) {
  const noPreference = window.matchMedia // the stand-in from src/test/setup.ts
  vi.stubGlobal(
    'matchMedia',
    vi.fn<typeof window.matchMedia>((query) => ({ ...noPreference(query), matches: light })),
  )
}

afterEach(() => {
  localStorage.clear()
  vi.restoreAllMocks()
})

describe('readThemeChoice', () => {
  it('returns the saved choice', () => {
    localStorage.setItem(THEME_STORAGE_KEY, 'light')
    expect(readThemeChoice()).toBe('light')
  })

  it('falls back to "system" for nothing saved or an unknown value', () => {
    expect(readThemeChoice()).toBe('system')
    localStorage.setItem(THEME_STORAGE_KEY, 'purple')
    expect(readThemeChoice()).toBe('system')
  })

  it('falls back to "system" when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError')
    })
    expect(readThemeChoice()).toBe('system')
  })
})

describe('resolveTheme', () => {
  it('keeps an explicit choice', () => {
    osPrefersLight(true)
    expect(resolveTheme('dark')).toBe('dark')
    expect(resolveTheme('light')).toBe('light')
  })

  it('follows the OS for "system", dark when it has no light preference', () => {
    osPrefersLight(true)
    expect(resolveTheme('system')).toBe('light')
    osPrefersLight(false)
    expect(resolveTheme('system')).toBe('dark')
  })
})

describe('setThemeChoice', () => {
  it('saves the choice and sets data-theme on <html>', () => {
    setThemeChoice('light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')
    expect(document.documentElement.dataset.theme).toBe('light')
    setThemeChoice('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })

  it('still applies the theme when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked', 'QuotaExceededError')
    })
    setThemeChoice('light')
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
