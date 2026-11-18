import { expect, test } from './fixtures.ts'
import { THEMES, useTheme } from './helpers.ts'

// The audit log (U7.4) in a real browser, against the fake API's made-up week (mocks/audit.ts):
// rows that open, filters kept in the URL, "Load more", and a phone. axe checks every state.

const lines = (page: import('@playwright/test').Page) =>
  page.getByRole('list', { name: 'Audit log lines' }).getByRole('button')

for (const theme of THEMES) {
  test(`${theme} theme: open a line by keyboard, and see its SQL`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/settings/audit?outcome=unsafe_sql')

    const first = lines(page).first()
    await expect(first).toContainText("I couldn't answer that safely.")
    await first.focus()
    await page.keyboard.press('Enter')

    await expect(first).toHaveAttribute('aria-expanded', 'true')
    await expect(page.getByText('SELECT usename, passwd FROM pg_shadow').first()).toBeVisible()
    await expect(
      page.getByText('Access to system catalogs (pg_shadow) is not allowed.').first(),
    ).toBeVisible()
  })
}

test('filters apply at once, stay in the URL, and "Load more" reads older lines', async ({
  page,
}) => {
  await page.goto('/settings/audit')
  await expect(lines(page)).toHaveCount(50)
  await page.getByRole('button', { name: 'Load more' }).click()
  await expect(page.getByText(/^\d+ lines\.$/)).toBeVisible()

  await page.getByLabel('Action', { exact: true }).selectOption({ label: 'Member added' })
  await expect(page).toHaveURL(/\/settings\/audit\?action=member_added$/)
  await expect(lines(page)).toHaveCount(2)

  // The same view from a link, after a reload.
  await page.reload()
  await expect(page.getByLabel('Action', { exact: true })).toHaveValue('member_added')
  await expect(lines(page)).toHaveCount(2)
})

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the lines stack, open, and nothing scrolls sideways', async ({ page }) => {
    await page.goto('/settings/audit')

    const first = lines(page).first()
    await first.click()
    await expect(first).toHaveAttribute('aria-expanded', 'true')
    const overflow = await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBe(0)
  })
})
