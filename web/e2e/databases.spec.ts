import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// The database switcher (U7.1b part 2b) in a real browser, against the fake API: one saved
// database, "Sales", put in the fake API's storage before the page loads. axe checks every state.

const SALES_ID = '9a7c1e2f-0000-4000-8000-000000000001'

async function withSales(page: Page, { key = true } = {}) {
  await page.addInitScript(
    ({ id, key: withKey }) => {
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
      if (withKey) {
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
      }
    },
    { id: SALES_ID, key },
  )
}

const barSwitcher = (page: Page) =>
  page.getByRole('banner').getByRole('combobox', { name: 'Database' })

for (const theme of THEMES) {
  test(`${theme} theme: choose your database, ask it, and see it in the explorer`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await withSales(page)
    await page.goto('/')

    await barSwitcher(page).selectOption({ label: 'Sales' })
    await expect(page.getByText('Ask anything about Sales')).toBeVisible()

    await page.getByRole('textbox', { name: 'Your question' }).fill('Top clients?')
    await page.keyboard.press('Control+Enter')
    await expect(page).toHaveURL(/\/c\//)
    // The answer says its model and cost; the history says which database.
    await expect(page.getByText(/AI calls · .* tokens/).first()).toBeVisible()
    await expect(page.getByRole('region', { name: 'History' }).getByText('Sales')).toBeVisible()

    await page
      .getByRole('navigation', { name: 'Main' })
      .getByRole('link', { name: 'Explore' })
      .click()
    await expect(page).toHaveURL(new RegExp(`/explore\\?db=${SALES_ID}$`))
    await expect(page.getByText(/No descriptions yet/)).toBeVisible()
  })
}

test('without your own key, the cell says so and links to the key tab', async ({ page }) => {
  await withSales(page, { key: false })
  await page.goto('/')

  await barSwitcher(page).selectOption({ label: 'Sales' })
  await page.getByRole('textbox', { name: 'Your question' }).fill('Top clients?')
  await page.keyboard.press('Control+Enter')

  const cells = page.getByRole('list', { name: 'Questions and answers' })
  await expect(cells.getByText('Your own database needs your own LLM key.')).toBeVisible()
  await cells.getByRole('link', { name: 'Save your own key' }).click()
  await expect(page).toHaveURL(/\/settings\/key$/)
})

test('a shared explorer link opens its database and sets the switcher', async ({ page }) => {
  await withSales(page)
  await page.goto(`/explore/tables/customers?db=${SALES_ID}`)

  await expect(page.getByRole('heading', { level: 1, name: 'customers' })).toBeVisible()
  await expect(barSwitcher(page)).toHaveValue(SALES_ID)
  await page.getByRole('link', { name: 'All tables' }).click()
  await expect(page).toHaveURL(new RegExp(`\\?table=customers&db=${SALES_ID}$`))
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the switcher is at the top of the page, and the top bar keeps its height', async ({
    page,
  }) => {
    await withSales(page)
    await page.goto('/')

    const pageSwitcher = page.getByRole('main').getByRole('combobox', { name: 'Database' })
    await expect(pageSwitcher).toBeVisible()
    await expect(barSwitcher(page)).toBeHidden()
    const header = await page.getByRole('banner').boundingBox()
    const menu = await page.getByRole('navigation', { name: 'Main' }).boundingBox()
    expect((header?.height ?? 0) + (menu?.height ?? 0)).toBeLessThanOrEqual(170)

    await pageSwitcher.selectOption({ label: 'Sales' })
    await expect(page.getByText('Ask anything about Sales')).toBeVisible()
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
