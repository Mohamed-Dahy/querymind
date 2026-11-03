import { expect, test } from '@playwright/test'

import { apiStatusLoaded, seriousA11yIssues, THEMES, useTheme } from './helpers.ts'

// Every route, with its page heading and tab title. /styleguide exists because this is a dev server.
const ROUTES = [
  { path: '/', heading: 'Ask about your data', title: 'Canvas · QueryMind' },
  {
    path: '/c/3f2a9c1e-0000-4000-8000-000000000000',
    heading: 'Ask about your data',
    title: 'Canvas · QueryMind',
  },
  { path: '/explore', heading: 'Data explorer', title: 'Data explorer · QueryMind' },
  {
    path: '/explore/glossary',
    heading: 'Business glossary',
    title: 'Business glossary · QueryMind',
  },
  { path: '/explore/tables/orders', heading: 'orders', title: 'Data explorer · QueryMind' },
  { path: '/evals', heading: 'Evals lab', title: 'Evals lab · QueryMind' },
  { path: '/no/such/page', heading: 'Page not found', title: 'Page not found · QueryMind' },
  { path: '/styleguide', heading: 'QueryMind · style guide', title: 'QueryMind' },
]

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
  for (let i = 0; i < 9; i++) {
    await page.keyboard.press('Tab')
    stops.push((await page.locator(':focus').innerText()).trim())
  }
  expect(stops).toEqual([
    'Skip to content',
    'QueryMind',
    'New conversation',
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
  await expect(page.getByRole('article', { name: /^one/ })).toBeVisible()
  await expect(box).toHaveValue('')
})

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    // Saved before the page loads, like a returning user's choice: index.html applies it first.
    test.beforeEach(async ({ page }) => {
      await useTheme(page, theme)
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
