import { expect, test, type Locator } from '@playwright/test'

import answer from '../src/mocks/recorded/answer.json' with { type: 'json' }
import { apiStatusLoaded, seriousA11yIssues, THEMES, useTheme } from './helpers.ts'

// The canvas flow in a real browser, against the mock API. U1.7 and U1.8 add errors and follow-ups
// to this file.
for (const theme of THEMES) {
  test(`${theme} theme: ask an example, get the answer cell, see the chart, table and SQL`, async ({
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
    // The chart opens first: a bar chart drawn in SVG, one image to screen readers with its summary.
    const chart = cell.getByRole('img', {
      name: /^Bar chart of total_sales by category_name, 8 bars/,
    })
    await expect(chart).toBeVisible()
    await expect(chart.locator('.recharts-bar-rectangle')).toHaveCount(answer.rows.length)
    // The bars use our --signal token (read from the page, so it follows the theme).
    const signal = await page.evaluate(
      "getComputedStyle(document.documentElement).getPropertyValue('--signal').trim()",
    )
    await expect(chart.locator('.recharts-bar-rectangle path').first()).toHaveAttribute(
      'fill',
      String(signal),
    )
    expect(await seriousA11yIssues(page)).toEqual([])

    await cell.getByRole('tab', { name: 'Table' }).click()
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

test('sales per month get a line chart; one number gets no chart and opens on the table', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('What were the total sales per month in 1997?')
  await box.press('Control+Enter')
  const monthly = page.getByRole('article', {
    name: 'What were the total sales per month in 1997?',
  })
  const line = monthly.getByRole('img', { name: /^Line chart of total_sales by month, 12 points/ })
  await expect(line).toBeVisible()
  await expect(line.locator('.recharts-line-dot, .recharts-dot')).toHaveCount(12)
  expect(await seriousA11yIssues(page)).toEqual([])

  // A new conversation (a fresh page load; U1.8 adds the button): in the same one it would be a
  // follow-up, which the mock answers with the recorded follow-up.
  await page.goto('/')
  await apiStatusLoaded(page)
  await box.fill('How many customers are there?')
  await box.press('Control+Enter')
  const single = page.getByRole('article', { name: 'How many customers are there?' })
  await expect(single.getByText('There are 91 customers in total.')).toBeVisible()
  await expect(single.getByRole('tab', { name: 'Chart' })).toHaveCount(0)
  await expect(single.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true')
  await expect(single.getByRole('table')).toBeVisible()
})

for (const theme of THEMES) {
  test(`${theme} theme: a question back, answered by a click and in your own words; a refusal`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const box = page.getByRole('textbox', { name: 'Your question' })

    await box.fill('Which products are the most popular?')
    await box.press('Control+Enter')
    const asked = page.getByRole('article', {
      name: 'Which products are the most popular?',
      exact: true,
    })
    const option = asked.getByRole('button', { name: 'by total revenue' })
    await expect(option).toBeVisible()
    await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
    expect(await seriousA11yIssues(page)).toEqual([])

    await option.click()
    const answered = page.getByRole('article', {
      name: 'Which products are the most popular? (by total revenue)',
    })
    await expect(answered.getByRole('tab', { name: 'SQL' })).toBeVisible()

    await asked.getByRole('button', { name: 'Something else…' }).click()
    const own = asked.getByRole('textbox', { name: 'Your answer' })
    await expect(own).toBeFocused()
    expect(await seriousA11yIssues(page)).toEqual([])
    await own.fill('by returns')
    await own.press('Enter')
    await expect(
      page.getByRole('article', { name: 'Which products are the most popular? (by returns)' }),
    ).toBeVisible()
    await expect(page.getByRole('article')).toHaveCount(3)

    // A new conversation, since the fake API only refuses a first question.
    await page.goto('/')
    await apiStatusLoaded(page)
    await box.fill('Delete all orders from 1996')
    await box.press('Control+Enter')
    const refused = page.getByRole('article', { name: 'Delete all orders from 1996' })
    await expect(refused.getByText('Nothing in the database was changed.')).toBeVisible()
    expect(await seriousA11yIssues(page)).toEqual([])
  })
}

test('on a phone, the sticky composer hides neither a question back nor the box that takes focus', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })
  const composer = page.locator('form').filter({ has: box })
  // Pixels between the bottom of the target and the top of the composer; negative means hidden.
  const gapAbove = (target: Locator) => async () => {
    const [own, sticky] = await Promise.all([target.boundingBox(), composer.boundingBox()])
    return own && sticky ? sticky.y - (own.y + own.height) : -1
  }

  await box.fill('Which products are the most popular?')
  await box.press('Control+Enter')
  const toggle = page.getByRole('button', { name: 'Something else…' })
  await expect(toggle).toBeVisible()
  await expect.poll(gapAbove(toggle)).toBeGreaterThanOrEqual(0)

  await toggle.press('Enter')
  const own = page.getByRole('textbox', { name: 'Your answer' })
  await expect(own).toBeFocused()
  await expect.poll(gapAbove(own)).toBeGreaterThanOrEqual(0)
})
