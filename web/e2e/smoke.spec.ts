import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import openapi from '../openapi.json' with { type: 'json' }

// The app in a real browser, against the mock API (playwright.config.ts): no uvicorn needed.
const VERSION = openapi.info.version
const THEMES = ['dark', 'light'] as const

/** Problems axe finds that block people (serious or critical), under WCAG 2.1 A and AA. */
async function seriousA11yIssues(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  // Just the rule and the elements, so a failure says what to fix.
  return results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => ({ rule: v.id, help: v.help, nodes: v.nodes.map((n) => n.target.join(' ')) }))
}

test('the page loads and shows the API version from the mock API', async ({ page }) => {
  const health = page.waitForResponse('**/api/health')
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'QueryMind', level: 1 })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText(`API ok · version ${VERSION}`)
  // Answered by MSW's service worker, not by a real API that happens to be running.
  expect((await health).fromServiceWorker()).toBe(true)
})

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    // Saved before the page loads, like a returning user's choice: index.html applies it first.
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((choice) => localStorage.setItem('querymind-theme', choice), theme)
    })

    test('the home page has no serious accessibility issues', async ({ page }) => {
      await page.goto('/')
      // Wait for the loaded state, so axe checks what people see (not the spinner).
      await expect(page.getByRole('status')).toContainText(VERSION)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)

      expect(await seriousA11yIssues(page)).toEqual([])
    })

    test('the style guide has no serious accessibility issues', async ({ page }) => {
      await page.goto('/?styleguide')
      await expect(page.getByRole('heading', { name: 'QueryMind · style guide' })).toBeVisible()

      expect(await seriousA11yIssues(page)).toEqual([])
    })
  })
}
