import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Permissions (U7.2) in a real browser against the fake API: the tables each role may use, a
// member's rows, the "your rows" chip and the cell for a role with no table. axe checks every state.

/** Log the fake login in with another role from the next page load on. */
async function asRole(page: Page, role: 'viewer' | 'analyst') {
  await page.evaluate((chosen) => localStorage.setItem('querymind-mock-role', chosen), role)
}

for (const theme of THEMES) {
  test(`${theme} theme: hide a table from viewers, and a viewer no longer sees it`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/settings/permissions')

    await page.getByRole('checkbox', { name: 'Viewers may use every table' }).uncheck()
    await page.getByRole('checkbox', { name: 'Viewers may use orders' }).uncheck()
    await page.getByRole('button', { name: 'Save viewers' }).click()
    await expect(page.getByText('Viewers: saved.')).toBeVisible()

    await asRole(page, 'viewer')
    await page.goto('/explore')
    const list = page.getByRole('region', { name: 'List of tables' })
    await expect(list.getByRole('button', { name: /^customers / })).toBeVisible()
    await expect(list.getByRole('button', { name: /^orders / })).toBeHidden()
  })
}

test('a member’s rows, set in a dialog on the Members tab', async ({ page }) => {
  await page.goto('/settings/members')

  await page.getByRole('button', { name: 'Edit rows of lee@example.com' }).click()
  const dialog = page.getByRole('dialog', { name: 'Rows for lee@example.com' })
  await dialog.getByRole('radio', { name: 'Only the rows tagged:' }).check()
  await dialog.getByRole('checkbox', { name: 'Western' }).check()
  await dialog.getByRole('button', { name: 'Save rows' }).click()

  await expect(page.getByText('lee@example.com sees the rows tagged Western.')).toBeVisible()
  await expect(page.getByRole('listitem', { name: /lee@example.com/ })).toContainText(
    'Only rows tagged Western',
  )
})

test('in a workspace where your rows are limited, answers that read them say so', async ({
  page,
}) => {
  await page.goto('/')
  const bar = page.getByRole('banner')
  await bar.getByRole('button', { name: 'Account' }).click()
  await bar.getByRole('button', { name: /Switch to Acme Sales/ }).click()
  await expect(page.getByText('Switched to Acme Sales.')).toBeVisible()

  // The recorded monthly answer reads orders: row-secured on the demo.
  await page
    .getByRole('textbox', { name: 'Your question' })
    .fill('What were the total sales per month in 1997?')
  await page.keyboard.press('Control+Enter')
  const cells = page.getByRole('list', { name: 'Questions and answers' })
  await expect(cells.getByText('Limited to your rows: Eastern')).toBeVisible()
})

test('a role with no table of this database is told who can grant access', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem(
      'querymind-mock-table-access',
      JSON.stringify({ demo: { analyst: null, viewer: [] } }),
    )
    localStorage.setItem('querymind-mock-role', 'viewer')
  })
  await page.goto('/')

  await page.getByRole('textbox', { name: 'Your question' }).fill('How many customers are there?')
  await page.keyboard.press('Control+Enter')
  const cells = page.getByRole('list', { name: 'Questions and answers' })
  await expect(cells.getByText(/Your role can’t use any table of this database/)).toContainText(
    "Ask an admin of analyst's workspace for access",
  )
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the permissions grid fits, and nothing scrolls sideways', async ({ page }) => {
    await page.goto('/settings/permissions')

    await expect(page.getByRole('checkbox', { name: 'Viewers may use every table' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Save viewers' })).toBeVisible()
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
