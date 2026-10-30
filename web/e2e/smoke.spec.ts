import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

import openapi from '../openapi.json' with { type: 'json' }

// The app in a real browser, against the mock API (playwright.config.ts): no uvicorn needed.
const VERSION = openapi.info.version
const THEMES = ['dark', 'light'] as const

// Every route, with its page heading and tab title. /styleguide exists because this is a dev server.
const ROUTES = [
  { path: '/', heading: 'Ask about your data', title: 'Canvas · QueryMind' },
  { path: '/c/3f2a9c1e', heading: 'Ask about your data', title: 'Canvas · QueryMind' },
  { path: '/explore', heading: 'Data explorer', title: 'Data explorer · QueryMind' },
  { path: '/evals', heading: 'Evals lab', title: 'Evals lab · QueryMind' },
  { path: '/no/such/page', heading: 'Page not found', title: 'Page not found · QueryMind' },
  { path: '/styleguide', heading: 'QueryMind · style guide', title: 'QueryMind' },
]

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

/** The shell's health line has loaded (so checks see the page people see, not the spinner). */
async function apiStatusLoaded(page: Page) {
  await expect(page.getByRole('status')).toHaveText(`API ok · version ${VERSION}`)
}

test('the page loads and shows the API version from the mock API', async ({ page }) => {
  const health = page.waitForResponse('**/api/health')
  await page.goto('/')

  await expect(page.getByRole('link', { name: 'QueryMind' })).toBeVisible()
  await apiStatusLoaded(page)
  // Answered by MSW's service worker, not by a real API that happens to be running.
  expect((await health).fromServiceWorker()).toBe(true)
})

test('every route works as a deep link', async ({ page }) => {
  for (const { path, heading, title } of ROUTES) {
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    await expect(page).toHaveTitle(title)
  }
})

test('the back and forward buttons move between pages', async ({ page }) => {
  await page.goto('/')
  const nav = page.getByRole('navigation', { name: 'Main' })
  await nav.getByRole('link', { name: 'Explore' }).click()
  await nav.getByRole('link', { name: 'Evals' }).click()
  await expect(page).toHaveURL(/\/evals$/)

  await page.goBack()
  await expect(page).toHaveURL(/\/explore$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Data explorer' })).toBeVisible()

  await page.goBack()
  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()

  await page.goForward()
  await expect(page).toHaveURL(/\/explore$/)
})

test('keyboard only: skip link first, then every control, and Enter navigates', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)

  // Walk the page with Tab and write down what gets focus, in order.
  const stops: string[] = []
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab')
    stops.push((await page.locator(':focus').innerText()).trim())
  }
  expect(stops).toEqual([
    'Skip to content',
    'QueryMind',
    'Dark',
    'Light',
    'System',
    'Canvas',
    'Explore',
    'Evals',
  ])

  // Enter on a rail link: the page changes, and focus lands on <main> (not left on the old link).
  await page.getByRole('link', { name: 'Explore' }).focus()
  await page.keyboard.press('Enter')
  await expect(page).toHaveURL(/\/explore$/)
  await expect(page.getByRole('main')).toBeFocused()
})

test('the skip link moves focus to the page content', async ({ page }) => {
  await page.goto('/')
  // Wait for the app: in mock mode it renders only after the mock API has started.
  await apiStatusLoaded(page)
  await page.keyboard.press('Tab')
  // Focused, not just "visible": Playwright counts the hidden (1 px, sr-only) link as visible.
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()

  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()
})

test('the composer grows with the text, explains errors, and sends with Ctrl+Enter', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await page.getByRole('button', { name: 'Ask' }).click()
  await expect(page.getByRole('alert')).toHaveText('Type a question first.')
  expect(await seriousA11yIssues(page)).toEqual([])

  await box.fill('one line')
  const oneLine = (await box.boundingBox())?.height ?? 0
  await box.fill('one\ntwo\nthree\nfour\nfive\nsix')
  // field-sizing: content (CSS) makes the box taller; no JavaScript measures it.
  expect((await box.boundingBox())?.height ?? 0).toBeGreaterThan(oneLine)

  await box.press('Control+Enter')
  await expect(page.getByText(/Received: “one/)).toBeVisible()
  await expect(box).toHaveValue('')
})

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    // Saved before the page loads, like a returning user's choice: index.html applies it first.
    test.beforeEach(async ({ page }) => {
      await page.addInitScript((choice) => localStorage.setItem('querymind-theme', choice), theme)
    })

    for (const { path, heading } of ROUTES) {
      test(`${path} has no serious accessibility issues`, async ({ page }) => {
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        if (path !== '/styleguide') await apiStatusLoaded(page)

        expect(await seriousA11yIssues(page)).toEqual([])
      })
    }
  })
}
