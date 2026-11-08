import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Logins in a real browser (U7.1), against the fake API (src/mocks/handlers.ts): any email and
// password work except "wrong password"; "Log out" sets a flag so a reload stays logged out, as a
// cleared cookie would. axe checks the page after every test (fixtures.ts).

/** Open the app logged out: no session, and the fake refresh cookie is gone. */
async function startLoggedOut(page: Page) {
  await page.addInitScript(() => localStorage.setItem('querymind-mock-logged-out', '1'))
}

test('logged out, the canvas asks you to log in and brings you back', async ({ page }) => {
  await startLoggedOut(page)
  await page.goto('/explore')

  await expect(page).toHaveURL(/\/login\?next=%2Fexplore$/)
  await expect(page).toHaveTitle('Log in · QueryMind')
  await page.getByLabel('Email').fill('ana@example.com')
  await page.getByLabel('Password').fill('a long enough password')
  await page.getByRole('button', { name: 'Log in' }).click()

  await expect(page).toHaveURL(/\/explore$/)
  await expect(page.getByRole('banner')).toContainText('ana@example.com')
})

test('a reload keeps you logged in, and "Log out" keeps you out', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()

  // The token was only in memory: the reload gets a new one from the (fake) refresh cookie.
  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()

  await page.getByRole('banner').getByRole('button', { name: 'Log out' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'Log in' })).toBeVisible()
  await page.goto('/')
  await expect(page).toHaveURL(/\/login\?next=%2F$/)
})

test('a wrong password is said in words, next to the form', async ({ page }) => {
  await startLoggedOut(page)
  await page.goto('/login')

  await page.getByLabel('Email').fill('ana@example.com')
  await page.getByLabel('Password').fill('wrong password')
  await page.getByRole('button', { name: 'Log in' }).click()

  await expect(page.getByRole('alert')).toHaveText('Wrong email or password.')
  await expect(page).toHaveURL(/\/login$/)
})

test('the keyboard alone can log in', async ({ page }) => {
  await startLoggedOut(page)
  await page.goto('/login')
  await expect(page.getByRole('heading', { level: 1, name: 'Log in' })).toBeVisible()

  // Into the form, then only keys: type, Tab to the next field, Enter submits.
  await page.getByLabel('Email').focus()
  await page.keyboard.type('ana@example.com')
  await page.keyboard.press('Tab')
  await expect(page.getByLabel('Password')).toBeFocused()
  await page.keyboard.type('a long enough password')
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/$/)
})

test('the evals lab is open to everyone, with "Log in" instead of history', async ({ page }) => {
  await startLoggedOut(page)
  await page.goto('/evals')

  await expect(page.getByRole('heading', { level: 1, name: 'Evals lab' })).toBeVisible()
  await expect(page.getByRole('banner').getByRole('link', { name: 'Log in' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'History' })).toHaveCount(0)
})

test('signing up opens your own canvas', async ({ page }) => {
  await startLoggedOut(page)
  await page.goto('/signup')

  await page.getByLabel('Email').fill('new@example.com')
  await page.getByLabel('Password').fill('a long enough password')
  await page.getByRole('button', { name: 'Create account' }).click()

  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('banner')).toContainText('new@example.com')
})

for (const theme of THEMES) {
  for (const path of ['/login', '/signup']) {
    test(`${path}, ${theme} theme: no accessibility problem, and it fits a phone`, async ({
      page,
    }) => {
      await useTheme(page, theme)
      await startLoggedOut(page)
      await page.setViewportSize({ width: 375, height: 800 })
      await page.goto(path)
      await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

      const overflow = (await page.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
      )) as number
      expect(overflow).toBeLessThanOrEqual(0)
      // axe runs after this, in both themes (fixtures.ts).
    })
  }
}
