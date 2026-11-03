import { expect, test } from '@playwright/test'

import { seriousA11yIssues, THEMES, useTheme } from './helpers.ts'

type Box = { x: number; y: number; width: number; height: number }

const overlap = (a: Box, b: Box) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height

test.describe('at 1440 px', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  for (const theme of THEMES) {
    test(`${theme} theme: every table in the graph, none overlapping, both businesses`, async ({
      page,
    }) => {
      await useTheme(page, theme)
      await page.goto('/explore')

      const cards = page.locator('.react-flow__node-table')
      await expect(cards).toHaveCount(25)
      await expect(page.locator('.react-flow__edge').first()).toBeAttached()

      const boxes =
        (await page.evaluate(`[...document.querySelectorAll('.react-flow__node-table')].map((node) => {
        const { x, y, width, height } = node.getBoundingClientRect()
        return { name: node.getAttribute('data-id'), x, y, width, height }
      })`)) as (Box & { name: string })[]
      const clashes = boxes.flatMap((a, index) =>
        boxes
          .slice(index + 1)
          .filter((b) => overlap(a, b))
          .map((b) => `${a.name} / ${b.name}`),
      )
      expect(clashes).toEqual([])
      expect(Math.min(...boxes.map(({ width }) => width))).toBeGreaterThan(0)

      const graph = page.getByRole('img', { name: /^Graph of 25 tables/ })
      await expect(graph.getByText('Chinook', { exact: true })).toBeVisible()
      await expect(graph.getByText('Northwind', { exact: true })).toBeVisible()
      expect(await seriousA11yIssues(page)).toEqual([])
    })
  }

  test('clicking a table in the graph chooses it, and the list agrees', async ({ page }) => {
    await page.goto('/explore')
    const list = page.getByRole('region', { name: 'List of tables' })

    await page.locator('.react-flow__node-table[data-id="orders"]').click()

    await expect(page).toHaveURL(/\?table=orders$/)
    await expect(list.getByRole('button', { name: /^orders/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await expect(page.getByRole('img', { name: /orders is highlighted/ })).toBeAttached()
    expect(await seriousA11yIssues(page)).toEqual([])

    await page.goBack()
    await expect(list.getByRole('button', { pressed: true })).toHaveCount(0)
  })

  test('a table chosen in the graph scrolls into view in the list', async ({ page }) => {
    await page.goto('/explore')
    const row = page
      .getByRole('region', { name: 'List of tables' })
      .getByRole('button', { name: /^us_states/ })
    await expect(row).not.toBeInViewport()

    await page.locator('.react-flow__node-table[data-id="us_states"]').click()

    await expect(row).toHaveAttribute('aria-pressed', 'true')
    await expect(row).toBeInViewport()
  })

  test('a shared link opens with its table chosen in the graph too', async ({ page }) => {
    await page.goto('/explore?table=track')

    await expect(
      page.getByRole('img', { name: /track is highlighted, with the 5 tables/ }),
    ).toBeAttached()
    await expect(
      page.locator('.react-flow__node-table[data-id="track"] .border-signal'),
    ).toBeVisible()
  })

  test('the zoom buttons work, and the wheel still scrolls the page', async ({ page }) => {
    await page.goto('/explore')
    const viewport = page.locator('.react-flow__viewport')
    await expect(page.locator('.react-flow__node-table')).toHaveCount(25)
    const before = await viewport.getAttribute('style')

    await page.getByRole('button', { name: 'Zoom in' }).click()

    await expect(viewport).not.toHaveAttribute('style', before ?? '')
    await page.getByRole('button', { name: 'Fit the graph to its area' }).click()
    await expect(page.locator('.react-flow__node-table').first()).toBeVisible()
  })

  test('keyboard only: search, choose, follow a join; the graph takes no Tab stops', async ({
    page,
  }) => {
    await page.goto('/explore')
    await expect(page.locator('.react-flow__node-table')).toHaveCount(25)
    const list = page.getByRole('region', { name: 'List of tables' })

    await expect(
      page.locator('.react-flow [tabindex="0"], .react-flow a, .react-flow button'),
    ).toHaveCount(0)

    await list.getByRole('searchbox').focus()
    await page.keyboard.type('clients')
    await page.keyboard.press('Tab')
    await expect(list.getByRole('button', { name: /^customers/ })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\?table=customers$/)
    await expect(list.getByRole('button', { name: /^customers/ })).toBeFocused()
    const join = list.getByRole('button', { name: 'customer_customer_demo' })
    await expect(join).toBeVisible() // the row opens just after the URL changes

    await page.keyboard.press('Tab')
    await expect(join).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page).toHaveURL(/\?table=customer_customer_demo$/)
  })
})

test('on a phone: the list only, with no sideways scroll, and the graph is never downloaded', async ({
  page,
}) => {
  const asked: string[] = []
  page.on('request', (request) => asked.push(request.url()))
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/explore')

  const list = page.getByRole('region', { name: 'List of tables' })
  await expect(list.getByText('25 tables')).toBeVisible()
  await expect(page.locator('.react-flow')).toHaveCount(0)
  expect(asked.filter((url) => /constellation|xyflow/i.test(url))).toEqual([])

  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    ),
  ).toBeLessThanOrEqual(0)
  expect(await seriousA11yIssues(page)).toEqual([])
})
