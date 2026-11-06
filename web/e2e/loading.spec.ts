import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { apiStatusLoaded, ROUTES } from './helpers.ts'

// "No screen is ever blank or frozen" (U6.2): with a page's own code and every API reply held back,
// the page still shows something, and then the real page arrives.

const HOLD_MS = 2_500

/** Every reply of the fake API waits this long (handlers.ts MOCK_DELAY_KEY). */
async function slowApi(page: Page, ms: number) {
  await page.addInitScript((wait) => localStorage.setItem('querymind-mock-delay', wait), String(ms))
}

/** The page's own code arrives only when `release` is called. */
async function holdCode(page: Page, file: string) {
  let release = () => {}
  const released = new Promise<void>((resolve) => (release = resolve))
  await page.context().route(`**/${file}*`, async (route) => {
    await released
    await route.continue()
  })
  return release
}

/** Where the page is drawn: <main>, or the whole page for the style guide, which has no shell. */
const pageArea = (page: Page, path: string) =>
  path === '/styleguide' ? page.locator('body') : page.getByRole('main')

for (const { path, heading, code } of ROUTES) {
  test(`${path} is never blank while its code and data load`, async ({ page }) => {
    await slowApi(page, HOLD_MS)
    const release = code ? await holdCode(page, code) : () => {}

    await page.goto(path)
    const area = pageArea(page, path)
    // Something people can see: the page's heading, or the shape of what is coming.
    await expect(area.locator('h1, [data-slot="skeleton"]').first()).toBeVisible()
    if (code) {
      // Still waiting for the code: the page's shape, and words for screen readers.
      await expect(area.getByRole('status').filter({ hasText: 'Loading the page…' })).toBeVisible()
    }

    release()
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
  })
}

test('before the app starts, the page says it is loading, in the right colours', async ({
  page,
}) => {
  const release = await holdCode(page, 'src/main.tsx')
  await page.addInitScript(() => localStorage.setItem('querymind-theme', 'dark'))
  // 'commit': the document has arrived. Later events wait for the app's code, which is held back.
  await page.goto('/', { waitUntil: 'commit' })

  await expect(page.getByText('Loading QueryMind…')).toBeVisible()
  const background = await page.evaluate('getComputedStyle(document.body).backgroundColor')
  expect(background).toBe('rgb(15, 23, 42)') // --bg in the dark theme (#0F172A), not white

  release()
  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()
})

test('opening the drawer on a slow connection says so at once', async ({ page }) => {
  const release = await holdCode(page, 'src/canvas/ShowYourWork.tsx')
  await page.goto('/')
  await apiStatusLoaded(page)
  await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()
  await page.getByRole('button', { name: 'Show your work' }).click()

  await expect(page.getByRole('status').filter({ hasText: 'Opening…' })).toBeVisible()

  release()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByText('Opening…')).toBeHidden()
})

test('opening the command palette on a slow connection says so at once', async ({ page }) => {
  const release = await holdCode(page, 'src/layout/CommandPalette.tsx')
  await page.goto('/')
  await apiStatusLoaded(page)
  await page.keyboard.press('Control+k')

  await expect(page.getByRole('status').filter({ hasText: 'Opening…' })).toBeVisible()

  release()
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible()
})

test('a first visit gets a hint above the examples; a returning one does not', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByText(/New here\? Pick a question below/)).toBeVisible()

  await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()
  await expect(page).toHaveURL(/\/c\//)
  await page.getByRole('banner').getByRole('button', { name: 'New conversation' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()
  await expect(page.getByText(/New here\?/)).toBeHidden()
})

test('downloading the CSV says so in a toast', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()
  await page.getByRole('tab', { name: 'Table' }).click()
  const download = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download CSV' }).click()

  const file = (await download).suggestedFilename()
  await expect(page.getByText(`Downloaded ${file}`)).toBeVisible()
})
