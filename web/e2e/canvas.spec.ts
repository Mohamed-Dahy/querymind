import { expect, test } from '@playwright/test'

import answer from '../src/mocks/recorded/answer.json' with { type: 'json' }
import { apiStatusLoaded, seriousA11yIssues, THEMES, useTheme } from './helpers.ts'

// The canvas flow in a real browser, against the mock API. U1.8 adds follow-ups, clarifications,
// refusals and errors to this file.
for (const theme of THEMES) {
  test(`${theme} theme: ask an example, get the answer cell, see the table and the SQL`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)

    await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()

    const cell = page.getByRole('article', { name: 'What is the total sales amount per category?' })
    await expect(cell.getByText(answer.answer)).toBeVisible()
    // The conversation now has its own URL, and the tab title is still the canvas's.
    await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
    await expect(page).toHaveTitle('Canvas · QueryMind')
    await expect(cell.getByRole('meter', { name: 'Confidence' })).toHaveAttribute(
      'aria-valuetext',
      'High',
    )
    await expect(cell.getByRole('table').getByRole('row')).toHaveCount(answer.rows.length + 1)
    expect(await seriousA11yIssues(page)).toEqual([])

    // The SQL tab loads Shiki on first open and colours the keywords with our tokens.
    const shiki = page.waitForResponse(/highlight/)
    await cell.getByRole('tab', { name: 'SQL' }).click()
    await shiki
    const select = cell.locator('code span span').first()
    await expect(select).toHaveText('SELECT')
    await expect(select).toHaveAttribute('style', /--shiki-token-keyword/)
    expect(await seriousA11yIssues(page)).toEqual([])
  })
}

test('the composer keeps focus while the URL changes, so the next question can be typed at once', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('Sales per category?')
  await box.press('Control+Enter')
  await expect(page).toHaveURL(/\/c\//)
  await expect(page.getByRole('article')).toHaveAttribute('aria-busy', 'false')

  await expect(box).toBeFocused()
  // Back skips the empty `/` (the URL was replaced, not pushed).
  await page.goBack()
  await expect(page).not.toHaveURL(/\/$/)
})

test('1,000 rows: only a window is drawn, scrolling reaches the last row, and sorting works', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })
  // Mock mode only: "rows: 1000" answers with 1,000 made-up rows.
  await box.fill('rows: 1000')
  await box.press('Control+Enter')

  const cell = page.getByRole('article', { name: 'rows: 1000' })
  await expect(cell.getByText(/Showing the first 1,000 rows/)).toBeVisible()
  const table = cell.getByRole('table')
  await expect(table).toHaveAttribute('aria-rowcount', '1001')
  expect(await table.locator('tbody tr[aria-rowindex]').count()).toBeLessThan(60)

  // Scroll to the bottom by keyboard (End): the last row is drawn, and still only a window of rows.
  const area = cell.getByRole('region', { name: 'Result rows' })
  await area.focus()
  await page.keyboard.press('End')
  await expect(table.locator('tr[aria-rowindex="1001"]')).toContainText('Category 1000')
  expect(await table.locator('tbody tr[aria-rowindex]').count()).toBeLessThan(60)

  // Sorting covers all 1,000 rows; the header stays readable while the rows scroll.
  await page.keyboard.press('Home')
  await cell.getByRole('button', { name: 'category_name' }).click()
  await expect(cell.getByRole('columnheader', { name: 'category_name' })).toHaveAttribute(
    'aria-sort',
    'ascending',
  )
  await expect(table.locator('tr[aria-rowindex="2"]')).toContainText('Category 1')
  expect(await seriousA11yIssues(page)).toEqual([])
})
