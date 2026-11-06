import { expect, test } from './fixtures.ts'
import { apiStatusLoaded, a11yIssues, THEMES, useTheme } from './helpers.ts'

// The ⌘K / Ctrl+K command palette, in a real browser against the fake API.

test('keyboard only: Ctrl+K, type, Enter opens the table, focus goes to the new page', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)

  await page.keyboard.press('Control+k')
  const dialog = page.getByRole('dialog', { name: 'Command palette' })
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('combobox')).toBeFocused()

  await page.keyboard.type('customers')
  await expect(dialog.getByRole('option', { selected: true })).toHaveAccessibleName(/^customers/)
  await page.keyboard.press('Enter')

  await expect(page).toHaveURL(/\/explore\/tables\/customers$/)
  await expect(page.getByRole('heading', { level: 1, name: 'customers' })).toBeVisible()
  await expect(page.locator('#main')).toBeFocused()
})

test('arrow keys move the choice, Escape closes and gives focus back', async ({ page }) => {
  await page.goto('/')
  const composer = page.getByRole('textbox', { name: 'Your question' })
  await composer.focus()

  await page.keyboard.press('Control+k')
  const dialog = page.getByRole('dialog', { name: 'Command palette' })
  const selected = dialog.getByRole('option', { selected: true })
  await expect(selected).toHaveAccessibleName(/New conversation/)
  await page.keyboard.press('ArrowDown')
  await expect(selected).toHaveAccessibleName(/Dark theme/)

  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
  await expect(composer).toBeFocused()
})

test('the Search button in the top bar opens it too', async ({ page }) => {
  await page.goto('/explore')
  await page
    .getByRole('banner')
    .getByRole('button', { name: /Search/ })
    .click()
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible()
})

for (const theme of THEMES) {
  test(`no accessibility problems, open and searching (${theme})`, async ({ page }) => {
    await useTheme(page, theme)
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')
    await apiStatusLoaded(page)

    await page.keyboard.press('Control+k')
    await expect(page.getByRole('option', { name: /New conversation/ })).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    await page.keyboard.type('late')
    await expect(page.getByRole('option', { name: /Late shipment/ })).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])
  })
}
