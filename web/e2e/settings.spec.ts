import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Settings → LLM key (U7.1b) in a real browser, against the fake API (src/mocks/handlers.ts and
// mocks/settings.ts): the key "gsk_refused_by_provider" is refused, "gsk_too_many_tries" is rate
// limited, and any other key of 8+ characters is saved. axe checks every state (fixtures.ts).

const keyField = (page: Page) => page.getByLabel('API key')
const saveButton = (page: Page) => page.getByRole('button', { name: 'Test and save' })

for (const theme of THEMES) {
  test(`${theme} theme: from the top bar to the key tab, then save a key`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/')

    await page.getByRole('banner').getByRole('button', { name: 'Account' }).click()
    await page.getByRole('banner').getByRole('link', { name: 'Settings' }).click()

    await expect(page).toHaveURL(/\/settings\/key$/)
    await expect(page).toHaveTitle('LLM key · Settings · QueryMind')
    await expect(page.getByText('QueryMind’s free demo key')).toBeVisible()

    await page.getByLabel('Provider').selectOption('gemini')
    await expect(page.getByRole('textbox', { name: 'Model' })).toHaveValue('gemini-3.5-flash')
    await keyField(page).fill('AIza-a-real-looking-key-WXYZ')
    await saveButton(page).click()

    await expect(page.getByText('Your own Google Gemini key')).toBeVisible()
    await expect(page.getByText('ends in …WXYZ')).toBeVisible()
    await expect(keyField(page)).toHaveValue('')
  })
}

test('a refused key, then too many tries: each says why, next to the form', async ({ page }) => {
  await page.goto('/settings/key')

  await keyField(page).fill('gsk_refused_by_provider')
  await saveButton(page).click()
  await expect(page.getByRole('alert')).toContainText("That key can't be used.")
  await expect(page.getByRole('alert')).toContainText('Groq refused this key.')
  await expect(keyField(page)).toHaveAttribute('aria-invalid', 'true')

  await keyField(page).fill('gsk_too_many_tries')
  await keyField(page).press('Enter')
  await expect(page.getByRole('alert')).toContainText('Too many keys tried in a short time.')
  await expect(page.getByText(/^Try again in \d+ seconds$/)).toBeVisible()
  await expect(saveButton(page)).toHaveAttribute('aria-disabled', 'true')
})

test('the key survives a reload; removing it asks first, and Escape cancels', async ({ page }) => {
  await page.goto('/settings/key')
  await keyField(page).fill('gsk_saved_for_later_1234')
  await saveButton(page).click()
  await expect(page.getByText('ends in …1234')).toBeVisible()

  await page.reload()
  await expect(page.getByText('Your own Groq key')).toBeVisible()

  const remove = page.getByRole('button', { name: 'Remove key' })
  await remove.click()
  const dialog = page.getByRole('dialog', { name: 'Remove your Groq key?' })
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(remove).toBeFocused()

  await remove.click()
  await dialog.getByRole('button', { name: 'Remove key' }).click()
  await expect(page.getByText('QueryMind’s free demo key')).toBeVisible()
  await expect(page.getByRole('heading', { level: 2, name: 'LLM key' })).toBeFocused()
})

test('the command palette finds the key tab by "api key"', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()

  await page.keyboard.press('Control+k')
  await page.getByRole('combobox').fill('api key')
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/settings\/key$/)
})

test('a viewer sees which key answers, but cannot change it', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('querymind-mock-role', 'viewer'))
  await page.goto('/settings')

  await expect(page).toHaveURL(/\/settings\/key$/)
  await expect(page.getByText('Only an admin of this workspace can save or remove')).toBeVisible()
  await expect(page.getByRole('form')).toHaveCount(0)
  await expect(page.getByRole('banner')).toContainText('Viewer')
})

// --- Databases (U7.1b part 2). The fake API refuses "private.example.com" (a private address)
// and the user "writer" (a login that can change data); anything else is saved.

async function addDatabase(page: Page, fields: Record<string, string>) {
  const form = page.getByRole('form', { name: 'Add a database' })
  for (const [label, value] of Object.entries(fields)) {
    await form.getByLabel(label, { exact: true }).fill(value)
  }
  await form.getByRole('button', { name: 'Check and add' }).click()
  return form
}

const SALES = {
  Name: 'Sales',
  Host: 'db.example.com',
  Database: 'sales',
  User: 'querymind_reader',
  Password: 'a long password',
}

for (const theme of THEMES) {
  test(`${theme} theme: add a database, see the recipe, then delete it`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/settings/databases')
    await expect(page.getByText('No database yet')).toBeVisible()

    await page.getByText('How to make a read-only login').click()
    await expect(page.getByText(/GRANT SELECT ON ALL TABLES/)).toBeVisible()

    const form = await addDatabase(page, SALES)
    const row = page.getByRole('listitem', { name: 'Sales' })
    await expect(row).toContainText('querymind_reader@db.example.com:5432/sales')
    await expect(form.getByLabel('Password')).toHaveValue('')
    // No key saved yet: asking about it needs one.
    await expect(page.getByRole('link', { name: /Save one in the LLM key tab/ })).toBeVisible()

    await row.getByRole('button', { name: 'Delete Sales' }).click()
    await page
      .getByRole('dialog', { name: 'Delete Sales?' })
      .getByRole('button', { name: 'Delete' })
      .click()
    await expect(page.getByText('No database yet')).toBeVisible()
    await expect(page.getByRole('heading', { level: 2, name: 'Databases' })).toBeFocused()
  })
}

test('a refused database says why, with its fix, and nothing is saved', async ({ page }) => {
  await page.goto('/settings/databases')

  const form = await addDatabase(page, { ...SALES, User: 'writer' })
  await expect(form.getByRole('alert')).toContainText('That login can change data.')
  await expect(form.getByRole('alert')).toContainText('REVOKE CREATE ON SCHEMA')

  await addDatabase(page, { ...SALES, User: 'querymind_reader', Host: 'private.example.com' })
  await expect(form.getByRole('alert')).toContainText("QueryMind can't connect to that address.")
  await expect(page.getByText('No database yet')).toBeVisible()
})

test('a viewer sees the databases, read-only', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('querymind-mock-role', 'viewer')
    localStorage.setItem(
      'querymind-mock-connections',
      JSON.stringify([
        {
          id: '9a7c1e2f-0000-4000-8000-000000000001',
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
  })
  await page.goto('/settings/databases')

  await expect(page.getByRole('listitem', { name: 'Sales' })).toBeVisible()
  await expect(page.getByRole('form')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /Delete|Test again/ })).toHaveCount(0)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 740 } })

  test('the account menu opens inside the screen, and leads to Settings', async ({ page }) => {
    await page.goto('/')
    const bar = page.getByRole('banner')
    await bar.getByRole('button', { name: 'Account' }).click()

    const settings = bar.getByRole('link', { name: 'Settings' })
    const box = await settings.boundingBox()
    expect(box!.x).toBeGreaterThanOrEqual(0)
    expect(box!.x + box!.width).toBeLessThanOrEqual(375)
    await settings.click()
    await expect(page).toHaveURL(/\/settings\/key$/)
  })

  test('the tabs are a row above the form, and the page does not scroll sideways', async ({
    page,
  }) => {
    await page.goto('/settings/key')
    const tabs = page.getByRole('navigation', { name: 'Settings' })
    const form = page.getByRole('form', { name: 'Save your own key' })
    await expect(form).toBeVisible()

    const tabsBox = await tabs.boundingBox()
    const formBox = await form.boundingBox()
    expect(tabsBox!.y + tabsBox!.height).toBeLessThanOrEqual(formBox!.y)
    // A string: the e2e files are typechecked without the browser's types (no `document`).
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
