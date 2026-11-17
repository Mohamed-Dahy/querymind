import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Hidden values (U7.3) and the limits' messages (U7.5) in a real browser, against the fake API:
// "contacts" in a question answers with personal-data columns, masked for every role but admin;
// "limit: questions_day" and the email too-many@example.com show a limit. axe checks every state.

/** Into Acme Sales, where the fake login is a viewer: personal data is hidden there. */
async function asViewerInAcme(page: Page) {
  const bar = page.getByRole('banner')
  await bar.getByRole('button', { name: 'Account' }).click()
  await bar.getByRole('button', { name: /Switch to Acme Sales/ }).click()
  await expect(page.getByText('Switched to Acme Sales.')).toBeVisible()
}

async function ask(page: Page, question: string) {
  await page.getByRole('textbox', { name: 'Your question' }).fill(question)
  await page.keyboard.press('Control+Enter')
}

for (const theme of THEMES) {
  test(`${theme} theme: a viewer sees "hidden" for personal data, and which columns`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await asViewerInAcme(page)

    await ask(page, 'Who are the contacts of our customers in Germany?')
    const cells = page.getByRole('list', { name: 'Questions and answers' })
    await expect(
      cells.getByText('2 columns hidden for your role: contact_name, contact_title.'),
    ).toBeVisible()
    const table = cells.getByRole('region', { name: 'Result rows' })
    await expect(
      table.getByRole('button', { name: 'contact_name (hidden for your role)' }),
    ).toBeVisible()
    await expect(table.getByText('hidden', { exact: true }).first()).toBeVisible()
    await expect(table.getByText('***')).toHaveCount(0)

    // The chip says why on hover.
    await table.getByText('hidden', { exact: true }).first().hover()
    await expect(page.getByRole('tooltip')).toContainText('Personal data, hidden for your role.')
  })
}

test('an admin sees the same answer with its values', async ({ page }) => {
  await page.goto('/')
  await ask(page, 'Who are the contacts of our customers in Germany?')

  const cells = page.getByRole('list', { name: 'Questions and answers' })
  await expect(cells.getByText('Maria Anders')).toBeVisible()
  await expect(cells.getByText(/hidden for your role/)).toHaveCount(0)
})

test('the day’s question limit says so, and when to come back', async ({ page }) => {
  await page.goto('/')
  await ask(page, 'limit: questions_day')

  const cell = page.getByRole('article', { name: 'limit: questions_day' })
  await expect(cell).toContainText("You've reached today's question limit.")
  await expect(cell).toContainText('You can ask 200 questions a day')
  await expect(cell.getByText(/^Try again in 5 hours (19|20) minutes$/)).toBeVisible()
  await expect(cell.getByRole('button', { name: 'Retry' })).toHaveAttribute('aria-disabled', 'true')
})

test('too many logins: the button waits out the limit', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('querymind-mock-logged-out', '1'))
  await page.goto('/login')

  await page.getByLabel('Email').fill('too-many@example.com')
  await page.getByLabel('Password').fill('a long enough password')
  await page.getByRole('button', { name: 'Log in' }).click()

  await expect(page.getByRole('alert')).toContainText('Too many login attempts.')
  await expect(page.getByText(/^Try again in \d+ seconds$/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Log in' })).toHaveAttribute(
    'aria-disabled',
    'true',
  )
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('hidden values fit: the table scrolls inside itself, the page doesn’t', async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem('querymind-mock-role', 'viewer'))
    await page.goto('/')
    await ask(page, 'Who are the contacts of our customers in Germany?')

    await expect(page.getByText(/2 columns hidden for your role/)).toBeVisible()
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
