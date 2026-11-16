import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Onboarding a saved database and reviewing its descriptions (U7.1c), in a real browser against
// the fake API: one saved database, "Sales", and the workspace's own key, put in the fake API's
// storage before the page loads. The fake run moves one stage per poll (every 1.5 s). axe checks
// every state.

const SALES_ID = '9a7c1e2f-0000-4000-8000-000000000002'

async function withSales(page: Page) {
  await page.addInitScript((id) => {
    localStorage.setItem(
      'querymind-mock-connections',
      JSON.stringify([
        {
          id,
          name: 'Sales',
          host: 'db.example.com',
          port: 5432,
          dbname: 'sales',
          user: 'querymind_reader',
          sslmode: 'verify-full',
          checked_at: '2026-11-16T10:00:00Z',
          created_at: '2026-11-16T10:00:00Z',
        },
      ]),
    )
    localStorage.setItem(
      'querymind-mock-llm-key',
      JSON.stringify({
        provider: 'groq',
        provider_label: 'Groq',
        model: 'openai/gpt-oss-120b',
        key_hint: '1234',
        updated_at: '2026-11-16T10:00:00Z',
      }),
    )
  }, SALES_ID)
}

/** Onboard Sales from its row and wait for the run to end. */
async function onboard(page: Page) {
  await page.goto('/settings/databases')
  const row = page.getByRole('listitem', { name: 'Sales' })
  await expect(row.getByText('Not onboarded yet.')).toBeVisible()
  await row.getByRole('button', { name: 'Onboard Sales' }).click()
  await expect(row.getByRole('list', { name: 'Onboarding' })).toBeVisible()
  await expect(row.getByRole('link', { name: 'Review descriptions of Sales' })).toBeVisible({
    timeout: 15_000,
  })
  await expect(row.getByText(/Last run: 13 tables drafted · 1 skipped\./)).toBeVisible()
  return row
}

for (const theme of THEMES) {
  test(`${theme} theme: onboard a database, review a table, and see it in the explorer`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await withSales(page)

    const row = await onboard(page)
    await row.getByRole('link', { name: 'Review descriptions of Sales' }).click()
    await expect(page).toHaveURL(new RegExp(`/settings/databases/${SALES_ID}/dictionary$`))
    await expect(
      page.getByRole('heading', { level: 2, name: 'Descriptions of Sales' }),
    ).toBeVisible()

    await page
      .getByRole('navigation', { name: 'Tables' })
      .getByRole('link', { name: 'shippers' })
      .click()
    const form = page.getByRole('form', { name: 'shippers' })
    await expect(form.getByRole('checkbox', { name: 'Personal data (phone)' })).toBeChecked()
    await form
      .getByRole('textbox', { name: 'company_name', exact: true })
      .fill('The shipping company’s name')
    await form.getByRole('button', { name: 'Save and mark reviewed' }).click()
    await expect(page.getByText('shippers: saved and marked reviewed.')).toBeVisible()
    await expect(form.getByText('Reviewed', { exact: true })).toBeVisible()

    // The explorer now has the database's descriptions (no "No descriptions yet" note).
    await page.goto(`/explore/tables/shippers?db=${SALES_ID}`)
    await expect(page.getByRole('heading', { level: 1, name: 'shippers' })).toBeVisible()
    await expect(page.getByText('The shipping company’s name')).toBeVisible()
  })
}

test('leaving the review with unsaved changes asks first', async ({ page }) => {
  await withSales(page)
  await onboard(page)
  await page.goto(`/settings/databases/${SALES_ID}/dictionary`)

  const form = page.getByRole('form', { name: 'categories' })
  await form.getByLabel('What one row is').fill('Product groups.')
  await page.getByRole('link', { name: 'Back to databases' }).click()

  const dialog = page.getByRole('dialog', { name: 'Leave without saving?' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('button', { name: 'Stay' }).click()
  await expect(form.getByLabel('What one row is')).toHaveValue('Product groups.')

  await page.getByRole('link', { name: 'Back to databases' }).click()
  await page
    .getByRole('dialog', { name: 'Leave without saving?' })
    .getByRole('button', { name: 'Leave' })
    .click()
  await expect(page).toHaveURL(/\/settings\/databases$/)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the tables are a menu above the form, and nothing scrolls sideways', async ({ page }) => {
    await withSales(page)
    await onboard(page)
    await page.goto(`/settings/databases/${SALES_ID}/dictionary`)

    await expect(page.getByRole('navigation', { name: 'Tables' })).toBeHidden()
    await page.getByRole('combobox', { name: 'Table' }).selectOption('customers')
    await expect(page).toHaveURL(/\?table=customers$/)
    const form = page.getByRole('form', { name: 'customers' })
    await expect(form.getByRole('textbox', { name: 'contact_name', exact: true })).toBeVisible()
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
