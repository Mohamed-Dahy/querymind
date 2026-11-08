import type { Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { a11yIssues, THEMES, useTheme } from './helpers.ts'

test('on a wide screen every number table fits, with no column cut off', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  for (const path of ['/evals/models', '/evals/ablations']) {
    await page.goto(path)
    await expect(page.getByRole('region', { name: /every number$/ }).first()).toBeVisible()
    const overflows = (await page.evaluate(
      `[...document.querySelectorAll('[role=region][aria-label$="every number"]')].map((box) => box.scrollWidth - box.clientWidth)`,
    )) as number[]
    expect(overflows.length).toBeGreaterThan(0)
    expect(Math.max(...overflows)).toBeLessThanOrEqual(0)
  }
})

test.describe('every question', () => {
  for (const theme of THEMES) {
    test(`${theme} theme: the matrix and a question have no accessibility problem`, async ({
      page,
    }) => {
      await useTheme(page, theme)
      await page.goto('/evals/questions')
      await expect(page.getByRole('region', { name: 'Questions against runs' })).toBeVisible()
      expect(await a11yIssues(page)).toEqual([])

      await page.goto('/evals/questions/b01_key_account_count')
      await expect(page.getByRole('region', { name: "Every run's verdict" })).toBeVisible()
      expect(await a11yIssues(page)).toEqual([])

      await page.goto('/evals/questions/bird_100')
      await expect(page.getByRole('link', { name: /Read them on BIRD/ })).toBeVisible()
      expect(await a11yIssues(page)).toEqual([])
    })
  }

  test('a failing question is two clicks from the evals lab', async ({ page }) => {
    await page.goto('/evals')
    await page.getByRole('link', { name: 'Questions', exact: true }).click()
    await page.getByRole('link', { name: 'How many key accounts do we have?' }).click()

    await expect(page.getByRole('heading', { name: 'Arm A: failed' })).toBeVisible()
    await expect(page.getByText("no column matches the values of 'count'")).toBeVisible()
  })

  test('so is a failing BIRD question', async ({ page }) => {
    await page.goto('/evals')
    await page.getByRole('link', { name: 'See every BIRD question' }).click()
    await page.getByRole('link', { name: 'bird_95', exact: true }).click()

    await expect(page.getByRole('region', { name: /: failed$/ })).toContainText(
      'BIRD EX: Failed. asked back instead of answering',
    )
  })

  test('the keyboard alone: Tab goes row to row, and Enter opens a question', async ({ page }) => {
    await page.goto('/evals/questions')
    const first = page.getByRole('link', { name: 'How many key accounts do we have?' })
    await first.focus()
    await page.keyboard.press('Tab')

    // The next stop is the next row's question, not the six cells beside this one.
    const focused = page.locator(':focus')
    await expect(focused).toHaveAttribute('href', /^\/evals\/questions\/[^?]+$/)
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/\/evals\/questions\/[^?]+$/)
    await expect(page.getByRole('region', { name: "Every run's verdict" })).toBeVisible()
  })

  test('filters stay in the URL through a reload', async ({ page }) => {
    await page.goto('/evals/questions')
    await page.getByRole('combobox', { name: 'Suite' }).selectOption('chinook')
    // Its state comes from the URL, a moment after the click, so not .check(), which looks at once.
    await page.getByRole('checkbox', { name: 'Failures only' }).click()
    await expect(page.getByRole('checkbox', { name: 'Failures only' })).toBeChecked()
    await expect(page).toHaveURL(/suite=chinook/)
    await expect(page).toHaveURL(/failures=1/)

    await page.reload()

    await expect(page.getByRole('combobox', { name: 'Suite' })).toHaveValue('chinook')
    await expect(page.getByRole('checkbox', { name: 'Failures only' })).toBeChecked()
  })

  for (const path of ['/evals/questions', '/evals/questions/b01_key_account_count']) {
    test(`on a phone ${path} has no sideways page scroll`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 800 })
      await page.goto(path)
      await expect(page.getByRole('region').first()).toBeVisible()

      const overflow = (await page.evaluate(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
      )) as number
      expect(overflow).toBeLessThanOrEqual(0)
    })
  }
})

test.describe('the model comparison', () => {
  for (const theme of THEMES) {
    test(`${theme} theme: no accessibility problem`, async ({ page }) => {
      await useTheme(page, theme)
      await page.goto('/evals/models')
      await expect(page.getByRole('img', { name: /^Scatter chart/ })).toBeVisible()
      await expect(page.locator('.recharts-scatter-symbol').first()).toBeVisible()

      expect(await a11yIssues(page)).toEqual([])
    })
  }

  test('draws one labelled point per model', async ({ page }) => {
    await page.goto('/evals/models')

    const chart = page.getByRole('img', { name: /^Scatter chart/ })
    await expect(chart.locator('.recharts-scatter-symbol')).toHaveCount(4)
    for (const name of ['gemini-3.5-flash-lite', 'qwen3.8-27b', 'gpt-oss-20b', 'gpt-oss-120b']) {
      await expect(chart.locator('text', { hasText: name })).toBeVisible()
    }
  })

  test('a phone reads it, and only the table scrolls sideways', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/evals/models')

    await expect(page.locator('.recharts-scatter-symbol')).toHaveCount(4)
    await expect(page.getByRole('listitem', { name: 'Speed of qwen3.8-27b' })).toContainText(
      'p95 29.4 s',
    )
    const overflow = (await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )) as number
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the keyboard reaches the view and scrolls the table', async ({ page }) => {
    await page.goto('/evals')
    const link = page.getByRole('link', { name: 'Models', exact: true })
    await link.focus()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/\/evals\/models$/)
    const table = page.getByRole('region', { name: /^Golden 58.*every number/ })
    await table.focus()
    await expect(table).toBeFocused()
  })
})

test.describe('what each part adds', () => {
  // Both studies draw bars named "Arm A"…, so look inside one of them.
  const golden = (page: Page) =>
    page.getByRole('region', { name: 'Golden 58: Northwind 30 + business terms 13 + Chinook 15' })

  for (const theme of THEMES) {
    test(`${theme} theme: no accessibility problem`, async ({ page }) => {
      await useTheme(page, theme)
      await page.goto('/evals/ablations')
      await expect(golden(page).getByRole('listitem', { name: 'Arm C' })).toBeVisible()

      expect(await a11yIssues(page)).toEqual([])
    })
  }

  test('a phone reads the bars, and only the table scrolls sideways', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/evals/ablations')

    await expect(golden(page).getByRole('listitem', { name: 'Arm C' })).toContainText('+20.7 pts')
    const overflow = (await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )) as number
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the keyboard reaches the suites and scrolls the table', async ({ page }) => {
    await page.goto('/evals/ablations')
    const business = page.getByRole('link', { name: 'Business terms 13' })
    await business.focus()
    await page.keyboard.press('Enter')

    await expect(page).toHaveURL(/scope=business/)
    await expect(golden(page).getByRole('listitem', { name: 'Arm A' })).toContainText('6/13')
    const table = page.getByRole('region', { name: /^Golden 58.*every number/ })
    await table.focus()
    await expect(table).toBeFocused()
  })
})

test.describe('the scoreboard', () => {
  for (const theme of THEMES) {
    test(`${theme} theme: no accessibility problem`, async ({ page }) => {
      await useTheme(page, theme)
      await page.goto('/evals')
      await expect(page.getByRole('listitem', { name: 'Golden 58' })).toBeVisible()

      expect(await a11yIssues(page)).toEqual([])
    })
  }

  test('a phone gets every number with no sideways scrolling', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 800 })
    await page.goto('/evals')
    await expect(page.getByRole('listitem', { name: 'Golden 58' })).toBeVisible()

    for (const name of [
      'Golden 58',
      'BIRD-100',
      'CI gate baseline',
      'Cheapest per correct answer',
    ]) {
      await expect(page.getByRole('listitem', { name })).toBeVisible()
    }
    const overflow = (await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )) as number
    expect(overflow).toBeLessThanOrEqual(0)
  })

  test('the page downloads its own code, not with the canvas', async ({ page }) => {
    const scripts: string[] = []
    page.on('request', (request) => {
      if (request.resourceType() === 'script') scripts.push(request.url())
    })
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1, name: 'Ask about your data' })).toBeVisible()
    expect(scripts.filter((url) => url.includes('Scoreboard'))).toEqual([])

    await page.getByRole('link', { name: 'Evals' }).click()

    await expect(page.getByRole('listitem', { name: 'Golden 58' })).toBeVisible()
    expect(scripts.filter((url) => url.includes('Scoreboard'))).not.toEqual([])
  })
})
