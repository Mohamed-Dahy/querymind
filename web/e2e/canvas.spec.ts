import type { Locator } from '@playwright/test'

import answer from '../src/mocks/recorded/answer.json' with { type: 'json' }
import { expect, test } from './fixtures.ts'
import { apiStatusLoaded, a11yIssues, THEMES, useTheme } from './helpers.ts'

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
    expect(await a11yIssues(page)).toEqual([])

    await cell.getByRole('tab', { name: 'Table' }).click()
    await expect(cell.getByRole('table').getByRole('row')).toHaveCount(answer.rows.length + 1)
    expect(await a11yIssues(page)).toEqual([])

    // The SQL tab loads Shiki on first open and colours the keywords with our tokens.
    const shiki = page.waitForResponse(/highlight/)
    await cell.getByRole('tab', { name: 'SQL' }).click()
    await shiki
    const select = cell.locator('code span span').first()
    await expect(select).toHaveText('SELECT')
    await expect(select).toHaveAttribute('style', /--shiki-token-keyword/)
    expect(await a11yIssues(page)).toEqual([])
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
  expect(await a11yIssues(page)).toEqual([])
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
  expect(await a11yIssues(page)).toEqual([])

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
    expect(await a11yIssues(page)).toEqual([])

    await option.click()
    const answered = page.getByRole('article', {
      name: 'Which products are the most popular? (by total revenue)',
    })
    await expect(answered.getByRole('tab', { name: 'SQL' })).toBeVisible()

    await asked.getByRole('button', { name: 'Something else…' }).click()
    const own = asked.getByRole('textbox', { name: 'Your answer' })
    await expect(own).toBeFocused()
    expect(await a11yIssues(page)).toEqual([])
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
    expect(await a11yIssues(page)).toEqual([])
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

for (const theme of THEMES) {
  test(`${theme} theme: a rate limit counts down before Retry; unsafe SQL offers Rephrase`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const box = page.getByRole('textbox', { name: 'Your question' })

    await box.fill('error: llm_rate_limited')
    await box.press('Control+Enter')
    const limited = page.getByRole('article', { name: 'error: llm_rate_limited' })
    await expect(limited.getByText('The AI model has reached its usage limit.')).toBeVisible()
    await expect(limited.getByText(/^Try again in \d+ seconds$/)).toBeVisible()
    await expect(limited.getByRole('button', { name: 'Retry' })).toHaveAttribute(
      'aria-disabled',
      'true',
    )
    expect(await a11yIssues(page)).toEqual([])

    await page.goto('/')
    await apiStatusLoaded(page)
    await box.fill('error: unsafe_sql')
    await box.press('Control+Enter')
    const unsafe = page.getByRole('article', { name: 'error: unsafe_sql' })
    await unsafe.getByRole('button', { name: 'Rephrase' }).click()
    await expect(box).toHaveValue('error: unsafe_sql')
    await expect(box).toBeFocused()
    expect(await a11yIssues(page)).toEqual([])
  })
}

// Milestone U1: everything the Streamlit app does, in one sitting, on the fake API.
for (const theme of THEMES) {
  test(`${theme} theme: the whole flow, from an example to a new conversation`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const box = page.getByRole('textbox', { name: 'Your question' })
    const newConversation = page.getByRole('button', { name: 'New conversation' })

    // An example question: the chart, the table and the SQL.
    const example = 'What is the total sales amount per category?'
    await page.getByRole('button', { name: example }).click()
    const first = page.getByRole('article', { name: example })
    await expect(first.getByRole('img', { name: /^Bar chart/ })).toBeVisible()
    await first.getByRole('tab', { name: 'Table' }).click()
    await expect(first.getByRole('table')).toBeVisible()
    await first.getByRole('tab', { name: 'SQL' }).click()
    await expect(first.getByText('SELECT', { exact: true }).first()).toBeVisible()
    await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
    const conversationUrl = page.url()

    // A follow-up: a linked cell, the same conversation.
    await first.getByRole('button', { name: 'Follow up' }).click()
    await expect(box).toBeFocused()
    await expect(page.getByText(`Following up on: ${example}`)).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])
    const asked = page.waitForRequest((request) => request.url().endsWith('/api/ask/stream'))
    await box.fill('Now only for 1997')
    await box.press('Control+Enter')
    expect((await asked).postDataJSON()).toEqual({
      question: 'Now only for 1997',
      conversation_id: conversationUrl.split('/').at(-1),
    })
    const linked = page.getByRole('article', { name: 'Now only for 1997' })
    await expect(linked.getByText(`Following up on: ${example}`)).toBeVisible()
    await expect(linked.getByRole('tab', { name: 'SQL' })).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    // New conversation: empty canvas, and Back still finds the old one.
    await newConversation.click()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('article')).toHaveCount(0)
    await expect(page.getByRole('region', { name: 'Northwind shop' })).toBeVisible()
    await page.goBack()
    await expect(page.getByRole('article')).toHaveCount(2)

    // A question back, answered with an option.
    await newConversation.click()
    await box.fill('Which products are the most popular?')
    await box.press('Control+Enter')
    await page.getByRole('button', { name: 'by total revenue' }).click()
    await expect(
      page.getByRole('article', {
        name: 'Which products are the most popular? (by total revenue)',
      }),
    ).toBeVisible()

    // A refusal.
    await newConversation.click()
    await box.fill('Delete all orders from 1996')
    await box.press('Control+Enter')
    await expect(page.getByText('Nothing in the database was changed.')).toBeVisible()

    // A rate limit: its countdown.
    await newConversation.click()
    await box.fill('error: llm_rate_limited')
    await box.press('Control+Enter')
    await expect(page.getByText(/^Try again in \d+ seconds$/)).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    // A reload: the conversation comes back from what was saved.
    await page.goto(conversationUrl)
    await expect(page.getByRole('article')).toHaveCount(2)
    await expect(page.getByRole('article', { name: example })).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])
  })
}

test('the app asks through the stream, and falls back to POST /ask when it breaks', async ({
  page,
}) => {
  const paths: string[] = []
  page.on('request', (request) => {
    const { pathname } = new URL(request.url())
    if (pathname.startsWith('/api/ask')) paths.push(pathname)
  })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('What is the total sales amount per category?')
  await box.press('Control+Enter')
  await expect(page.getByText(answer.answer)).toBeVisible()
  expect(paths).toEqual(['/api/ask/stream'])

  await page.getByRole('button', { name: 'New conversation' }).click()
  paths.length = 0
  await box.fill('stream: broken')
  await box.press('Control+Enter')
  await expect(
    page.getByRole('article', { name: 'stream: broken' }).getByText(answer.answer),
  ).toBeVisible()
  expect(paths).toEqual(['/api/ask/stream', '/api/ask'])
})

// U2.4: the agent rail, live.
for (const theme of THEMES) {
  test(`${theme} theme: the rail shows the repair loop, and Stop ends a run`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const box = page.getByRole('textbox', { name: 'Your question' })

    const question = 'How many customers are there in each country?'
    await box.fill(question)
    await box.press('Control+Enter')
    const repaired = page.getByRole('article', { name: question })
    const steps = repaired.getByRole('list', { name: 'Agent steps' })
    // Caught while it runs: the rail is there before the answer, and it moves.
    await expect(steps.locator('[aria-current="step"]')).toHaveCount(1)
    expect(await a11yIssues(page)).toEqual([])
    await expect(repaired.getByText('The USA has the most customers, with 13.')).toBeVisible()
    await expect(repaired.getByText('2 tries')).toBeVisible()
    await expect(repaired.getByText(/^Try 1 failed: .*GROUP BY/)).toBeVisible()
    await expect(steps.locator('[aria-current="step"]')).toHaveCount(0)
    expect(await a11yIssues(page)).toEqual([])

    await page.getByRole('button', { name: 'New conversation' }).click()
    await box.fill('stream: stuck')
    await box.press('Control+Enter')
    const stuck = page.getByRole('article', { name: 'stream: stuck' })
    await expect(stuck.locator('[aria-current="step"]')).toContainText('Write SQL')
    expect(await a11yIssues(page)).toEqual([])
    await stuck.getByRole('button', { name: 'Stop' }).click()
    await expect(stuck.getByText('You stopped this question.')).toBeVisible()
    await expect(stuck.getByRole('button', { name: 'Retry' })).toBeVisible()
    await expect(box).toBeFocused()
    expect(await a11yIssues(page)).toEqual([])
  })
}

test('under reduced motion the rail is still: no spinning, and the words say what is running', async ({
  page,
}) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('stream: stuck')
  await box.press('Control+Enter')

  const running = page.getByRole('article').locator('[aria-current="step"]')
  await expect(running).toContainText('running')
  const animation = await page.evaluate(
    "getComputedStyle(document.querySelector('[aria-current=step] svg')).animationName",
  )
  expect(animation).toBe('none')
})

test('on a phone the rail wraps without a sideways scroll, and Stop is reachable', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('How many customers are there in each country?')
  await box.press('Control+Enter')
  await expect(page.getByText(/^Try 1 failed: /)).toBeVisible()
  const overflow = await page.evaluate(
    'document.documentElement.scrollWidth - document.documentElement.clientWidth',
  )
  expect(overflow).toBeLessThanOrEqual(0)

  await page.getByRole('button', { name: 'New conversation' }).click()
  await box.fill('stream: stuck')
  await box.press('Control+Enter')
  await page.getByRole('button', { name: 'Stop' }).click()
  await expect(page.getByRole('article').getByText('You stopped this question.')).toBeVisible()
  expect(await a11yIssues(page)).toEqual([])
})

// U2.5: the drawer, on the recorded repair.
for (const theme of THEMES) {
  test(`${theme} theme: "Show your work" shows every try, with what changed`, async ({ page }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const box = page.getByRole('textbox', { name: 'Your question' })

    await box.fill('How many customers are there in each country?')
    await box.press('Control+Enter')
    const button = page.getByRole('button', { name: 'Show your work' })
    await button.click()

    const drawer = page.getByRole('dialog', { name: 'How this answer was made' })
    await expect(drawer.getByText(/^Retrieved \d+ of \d+ tables/)).toBeVisible()
    await expect(drawer.getByRole('heading', { name: 'The SQL, 2 tries' })).toBeVisible()
    await expect(drawer.getByText(/The database rejected it\./)).toBeVisible()
    await expect(drawer.locator('del')).toContainText('region')
    await expect(drawer.locator('ins').first()).toContainText('country')
    await expect(drawer.getByText('It needed 1 repair to get this result.')).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    await page.keyboard.press('Escape')
    await expect(drawer).toHaveCount(0)
    await expect(button).toBeFocused()
  })
}

test('on a phone the drawer fills the screen and scrolls, without a sideways scroll', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })

  await box.fill('How many customers are there in each country?')
  await box.press('Control+Enter')
  await page.getByRole('button', { name: 'Show your work' }).click()

  const drawer = page.getByRole('dialog', { name: 'How this answer was made' })
  // Waits for the drawer's own code to download first, then measures.
  await expect(drawer.getByRole('heading', { name: 'The SQL, 2 tries' })).toBeVisible()
  const size = await drawer.boundingBox()
  // Close, not exact: browsers measure in fractions of a pixel (CI's said 374.99998).
  expect(size?.width).toBeCloseTo(375, 0)
  const sideways = await page.evaluate(
    "(() => { const el = document.querySelector('[role=dialog]'); return el.scrollWidth - el.clientWidth })()",
  )
  expect(sideways).toBeLessThanOrEqual(0)
  await drawer.getByText('It needed 1 repair to get this result.').scrollIntoViewIfNeeded()
  await expect(drawer.getByText('It needed 1 repair to get this result.')).toBeInViewport()
  expect(await a11yIssues(page)).toEqual([])
})

// U3.4: history survives a reload.
for (const theme of THEMES) {
  test(`${theme} theme: ask, reload, it is still there, and a re-run brings the rows back`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const question = 'What is the total sales amount per category?'
    const box = page.getByRole('textbox', { name: 'Your question' })
    await box.fill(question)
    await box.press('Control+Enter')
    await expect(page.getByText(answer.answer)).toBeVisible()
    await expect(page).toHaveURL(/\/c\//)
    const history = page.getByRole('region', { name: 'History' })
    await expect(history.getByRole('link', { name: question })).toBeVisible()

    await page.reload()

    const cell = page.getByRole('article', { name: question })
    await expect(cell.getByText(answer.answer)).toBeVisible()
    await expect(cell.getByRole('list', { name: 'Agent steps' })).toBeVisible()
    await expect(history.getByRole('link', { name: question })).toHaveAttribute(
      'aria-current',
      'page',
    )
    await expect(cell.getByText(/aren't stored/)).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    await cell.getByRole('button', { name: 'Re-run for rows' }).click()
    await expect(cell.getByRole('img', { name: /^Bar chart/ })).toBeVisible()
    await expect(cell.getByText(/^Fresh rows from /)).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    // The drawer still works on a saved answer.
    await cell.getByRole('button', { name: 'Show your work' }).click()
    await expect(
      page.getByRole('dialog').getByRole('heading', { name: 'The SQL', exact: true }),
    ).toBeVisible()
  })
}

test('the history rail: search, rename, pin, delete and Undo', async ({ page }) => {
  // Five features in one test: 16 s alone, and 25–30 s when the suite runs in parallel, right at
  // the 30 s limit. slow() gives it three times as long.
  test.slow()
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })
  const history = page.getByRole('region', { name: 'History' })

  for (const question of ['Sales per category?', 'Top 3 artists']) {
    await page.getByRole('button', { name: 'New conversation' }).click()
    await box.fill(question)
    await box.press('Control+Enter')
    await expect(history.getByRole('link', { name: question })).toBeVisible()
  }

  await history.getByRole('searchbox').fill('artists')
  await expect(history.getByRole('link', { name: 'Sales per category?' })).toHaveCount(0)
  await history.getByRole('searchbox').fill('')
  const sales = history.getByRole('link', { name: 'Sales per category?' })
  await expect(sales).toBeVisible()

  await sales.hover()
  await history.getByRole('button', { name: 'Pin “Sales per category?”' }).click()
  await expect(history.getByRole('heading', { name: 'Pinned' })).toBeVisible()
  expect(await a11yIssues(page)).toEqual([])

  // By keyboard: focus shows the buttons, which are hidden until wanted.
  await page.getByRole('link', { name: 'Top 3 artists' }).focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  const title = history.getByRole('textbox', { name: 'New title' })
  await expect(title).toBeFocused()
  await title.fill('Weekly numbers')
  await title.press('Enter')
  const renamed = history.getByRole('link', { name: 'Weekly numbers' })
  await expect(renamed).toBeVisible()

  await renamed.hover()
  await history.getByRole('button', { name: 'Delete “Weekly numbers”' }).click()
  await expect(renamed).toHaveCount(0)
  await page.getByRole('button', { name: 'Undo' }).click()
  await expect(renamed).toBeVisible()

  await renamed.hover()
  await history.getByRole('button', { name: 'Delete “Weekly numbers”' }).click()
  await expect(renamed).toHaveCount(0)
  // Without an Undo, the real DELETE goes out when the time ends. (Closing the page sends it at
  // once, but the fake API lives in the page, so that path is tested in Vitest and on the real API.)
  await page.waitForRequest((request) => request.method() === 'DELETE')
  await page.reload()
  await apiStatusLoaded(page)
  await expect(history.getByRole('link', { name: 'Sales per category?' })).toBeVisible()
  await expect(history.getByRole('link', { name: 'Weekly numbers' })).toHaveCount(0)
})

// U3.5: a question kept to ask again.
for (const theme of THEMES) {
  test(`${theme} theme: save a question, it survives a reload, and one click asks it anew`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/')
    await apiStatusLoaded(page)
    const question = 'Sales per category?'
    const box = page.getByRole('textbox', { name: 'Your question' })
    await box.fill(question)
    await box.press('Control+Enter')
    const cell = page.getByRole('article', { name: question })
    await expect(cell.getByText(answer.answer)).toBeVisible()

    await cell.getByRole('button', { name: 'Save question' }).click()
    const saved = cell.getByRole('button', { name: 'Saved' })
    await expect(saved).toHaveAttribute('aria-pressed', 'true')
    const history = page.getByRole('region', { name: 'History' })
    const again = history.getByRole('button', { name: `Ask “${question}”` })
    await expect(again).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    await page.reload()
    await expect(again).toBeVisible()
    const firstUrl = page.url()

    await again.click()
    await expect(page.getByRole('article')).toHaveCount(1)
    await expect(
      page.getByRole('article', { name: question }).getByText(answer.answer),
    ).toBeVisible()
    await expect(page).toHaveURL(/\/c\//)
    expect(page.url()).not.toBe(firstUrl)

    await again.hover()
    await history.getByRole('button', { name: `Remove “${question}” from saved` }).click()
    await expect(again).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Save question' })).toBeVisible()
  })
}

test('on a phone a saved question is asked from the sheet, which then closes', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })
  await box.fill('Sales per category?')
  await box.press('Control+Enter')
  await page.getByRole('button', { name: 'Save question' }).click()
  await expect(page.getByRole('button', { name: 'Saved' })).toBeVisible()
  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    ),
  ).toBeLessThanOrEqual(0)
  await page.getByRole('button', { name: 'New conversation' }).click()

  await page.getByRole('button', { name: 'History' }).click()
  const sheet = page.getByRole('dialog', { name: 'History' })
  await sheet.getByRole('button', { name: 'Ask “Sales per category?”' }).click()

  await expect(sheet).toHaveCount(0)
  await expect(page.getByRole('article', { name: 'Sales per category?' })).toBeVisible()
})

test('on a phone the history opens in a sheet, and a conversation opens from it', async ({
  page,
}) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/')
  await apiStatusLoaded(page)
  const box = page.getByRole('textbox', { name: 'Your question' })
  await box.fill('Sales per category?')
  await box.press('Control+Enter')
  await expect(page.getByRole('article').getByText(answer.answer)).toBeVisible()
  await page.getByRole('button', { name: 'New conversation' }).click()

  const opener = page.getByRole('button', { name: 'History' })
  await opener.click()
  const sheet = page.getByRole('dialog', { name: 'History' })
  await expect(sheet.getByRole('link', { name: 'Sales per category?' })).toBeVisible()
  expect(await a11yIssues(page)).toEqual([])

  await sheet.getByRole('link', { name: 'Sales per category?' }).click()
  await expect(sheet).toHaveCount(0)
  await expect(page.getByRole('article', { name: 'Sales per category?' })).toBeVisible()

  await opener.click()
  await page.keyboard.press('Escape')
  await expect(opener).toBeFocused()
})
