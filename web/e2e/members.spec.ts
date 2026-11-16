import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// Members and the workspace switcher (U7.1d), in a real browser against the fake API. The fake
// login is the admin of its own workspace (with sam, an analyst, and lee, a viewer) and a viewer in
// Acme Sales. axe checks every state.

for (const theme of THEMES) {
  test(`${theme} theme: change a role, add a member and remove one`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/settings/members')

    await page.getByRole('combobox', { name: 'Role of sam@example.com' }).selectOption('viewer')
    await expect(page.getByText('sam@example.com is viewer now.')).toBeVisible()

    const form = page.getByRole('form', { name: 'Add a member' })
    await form.getByLabel('Email').fill('ana@example.com')
    await form.getByLabel('Role').selectOption('analyst')
    await form.getByRole('button', { name: 'Add' }).click()
    await expect(page.getByText('ana@example.com added as analyst.')).toBeVisible()
    await expect(page.getByRole('combobox', { name: 'Role of ana@example.com' })).toHaveValue(
      'analyst',
    )

    await page.getByRole('button', { name: 'Remove lee@example.com' }).click()
    const dialog = page.getByRole('dialog', { name: 'Remove lee@example.com?' })
    await dialog.getByRole('button', { name: 'Remove' }).click()
    await expect(page.getByRole('listitem', { name: /lee@example.com/ })).toBeHidden()
    await expect(page.getByRole('heading', { level: 2, name: 'Members' })).toBeFocused()
  })
}

test('an unknown email says they need to sign up first', async ({ page }) => {
  await page.goto('/settings/members')

  const form = page.getByRole('form', { name: 'Add a member' })
  await form.getByLabel('Email').fill('nobody@example.com')
  await form.getByRole('button', { name: 'Add' }).click()
  await expect(form.getByRole('alert')).toContainText('they need to sign up first')
})

test('switch workspace from the account menu: a viewer there, without the Members tab', async ({
  page,
}) => {
  await page.goto('/settings/members')
  await expect(page.getByRole('list', { name: 'Members' })).toBeVisible()

  const bar = page.getByRole('banner')
  await bar.getByRole('button', { name: 'Account' }).click()
  await bar.getByRole('button', { name: /Switch to Acme Sales/ }).click()

  await expect(page.getByText('Switched to Acme Sales.')).toBeVisible()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('banner')).toContainText('Role: Viewer')

  // A reload stays there (the fake refresh cookie was switched too).
  await page.goto('/settings/key')
  const tabs = page.getByRole('navigation', { name: 'Settings' })
  await expect(tabs.getByRole('link', { name: 'LLM key' })).toBeVisible()
  await expect(tabs.getByRole('link', { name: 'Members' })).toBeHidden()
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the members list and the add form fit, and nothing scrolls sideways', async ({ page }) => {
    await page.goto('/settings/members')

    await expect(page.getByRole('combobox', { name: 'Role of sam@example.com' })).toBeVisible()
    await expect(page.getByRole('form', { name: 'Add a member' })).toBeVisible()
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)

    await page.getByRole('banner').getByRole('button', { name: 'Account' }).click()
    await expect(page.getByRole('list', { name: 'Workspaces' })).toBeVisible()
  })
})
