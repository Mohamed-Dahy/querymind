import { AxeBuilder } from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

import openapi from '../openapi.json' with { type: 'json' }

// Shared by the e2e files. The app runs in a real browser against the mock API (playwright.config.ts).
export const VERSION = openapi.info.version
export const THEMES = ['dark', 'light'] as const

/** Problems axe finds that block people (serious or critical), under WCAG 2.1 A and AA. */
export async function seriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  // Just the rule and the elements, so a failure says what to fix.
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => ({ rule: v.id, help: v.help, nodes: v.nodes.map((n) => n.target.join(' ')) }))
}

/** The shell's health line has loaded (so checks see the page people see, not the spinner). */
export async function apiStatusLoaded(page: Page) {
  // In the top bar: a page can have status lines of its own (the question matrix's count).
  await expect(page.getByRole('banner').getByRole('status')).toHaveText(
    `API ok · version ${VERSION}`,
  )
}

/** Open the page with a saved theme, like a returning user's choice (index.html applies it first). */
export async function useTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.addInitScript((choice) => localStorage.setItem('querymind-theme', choice), theme)
}
