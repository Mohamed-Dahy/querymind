import { expect, test } from './fixtures.ts'
import { a11yIssues, THEMES, useTheme } from './helpers.ts'

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

      const graph = page.getByRole('group', { name: /^Graph of 25 tables/ })
      await expect(graph.getByText('Chinook', { exact: true })).toBeVisible()
      await expect(graph.getByText('Northwind', { exact: true })).toBeVisible()
      await expect(graph.getByRole('link', { name: 'React Flow' })).toBeVisible()
      expect(await a11yIssues(page)).toEqual([])
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
    await expect(page.getByRole('group', { name: /orders is highlighted/ })).toBeAttached()
    expect(await a11yIssues(page)).toEqual([])

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
      page.getByRole('group', { name: /track is highlighted, with the 5 tables/ }),
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

    await expect(page.locator('.react-flow [tabindex="0"], .react-flow button')).toHaveCount(0)
    await expect(page.locator('.react-flow a')).toHaveCount(1) // React Flow's credit

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
    await expect(list.getByRole('link', { name: 'Everything about customers' })).toBeFocused()
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
  expect(await a11yIssues(page)).toEqual([])
})

for (const theme of THEMES) {
  test(`${theme} theme: a table's page, from the list, through a join, to a question`, async ({
    page,
  }) => {
    await useTheme(page, theme)
    await page.goto('/explore?table=orders')
    await page.getByRole('link', { name: 'Everything about orders' }).click()

    await expect(page.getByRole('heading', { level: 1, name: 'orders' })).toBeVisible()
    await expect(
      page
        .getByRole('region', { name: 'Business terms' })
        .getByRole('link', { name: 'Late shipment in the glossary' }),
    ).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    const open = page.getByRole('link', { name: 'Open customers' }).first()
    await open.scrollIntoViewIfNeeded()
    await open.click()
    const heading = page.getByRole('heading', { level: 1, name: 'customers' })
    await expect(heading).toBeInViewport()

    const question = page
      .getByRole('region', { name: 'Questions to ask' })
      .getByRole('button')
      .first()
    const words = (await question.innerText()).trim()
    await question.click()

    const box = page.getByRole('textbox', { name: 'Your question' })
    await expect(box).toHaveValue(words)
    await expect(box).toBeFocused()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole('article')).toHaveCount(0)

    await page.goBack()
    await expect(heading).toBeVisible()
  })
}

test('on a phone a table page has no sideways scroll on the page itself', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/explore/tables/customer_customer_demo')
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Joins' })).toBeVisible()

  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    ),
  ).toBeLessThanOrEqual(0)
  expect(await a11yIssues(page)).toEqual([])
})

test('the glossary: from the explorer, found by another word, then asked with', async ({
  page,
}) => {
  await page.goto('/explore')
  await page.getByRole('link', { name: /^Business glossary/ }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Business glossary' })).toBeVisible()

  await page.getByRole('searchbox', { name: /^Search terms/ }).fill('turnover')
  const revenue = page.getByRole('listitem', { name: 'Revenue' })
  await expect(revenue).toBeVisible()
  await expect(page.getByRole('listitem')).not.toHaveCount(13)
  await revenue.getByRole('button', { name: 'Ask with this term' }).click()

  const box = page.getByRole('textbox', { name: 'Your question' })
  await expect(box).toHaveValue('What is our revenue?')
  await expect(box).toBeFocused()
  await expect(page.getByRole('article')).toHaveCount(0)
})

test('a term on a table page links to its place in the glossary, in view', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 500 })
  await page.goto('/explore/tables/track')
  const link = page.getByRole('link', { name: 'Unsold track in the glossary' })

  await link.click()

  await expect(page).toHaveURL(/#term-unsold_track$/)
  await expect(page.getByRole('listitem', { name: 'Unsold track' })).toBeInViewport()
})

test('on a phone the glossary has no sideways scroll on the page', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 700 })
  await page.goto('/explore/glossary')
  await expect(page.getByRole('listitem', { name: 'Revenue' })).toBeVisible()

  expect(
    await page.evaluate(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    ),
  ).toBeLessThanOrEqual(0)
  expect(await a11yIssues(page)).toEqual([])
})

for (const theme of THEMES) {
  test(`${theme} theme: answer, drawer, the tables lit, a table's page, and back to the question`, async ({
    page,
  }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await useTheme(page, theme)
    await page.goto('/')
    const question = 'What is the total sales amount per category?'
    const box = page.getByRole('textbox', { name: 'Your question' })
    await box.fill(question)
    await box.press('Control+Enter')
    const cell = page.getByRole('article', { name: question })
    await expect(cell.getByRole('button', { name: 'Show your work' })).toBeVisible()
    await expect(page).toHaveURL(/\/c\//)
    const conversation = page.url()

    await cell.getByRole('button', { name: 'Show your work' }).click()
    await page.getByRole('dialog').getByRole('link', { name: 'See these tables' }).click()

    await expect(page).toHaveURL(/\/explore\?/)
    await expect(page.getByRole('dialog')).toHaveCount(0)
    const card = (name: string) => page.locator(`.react-flow__node-table[data-id="${name}"]`)
    await expect(card('products')).toContainText('used')
    await expect(card('categories')).toContainText('used')
    await expect(card('region')).not.toContainText('used')
    await expect(page.getByText(/One answer's tables/)).toBeVisible()
    expect(await a11yIssues(page)).toEqual([])

    const list = page.getByRole('region', { name: 'List of tables' })
    await list.getByRole('button', { name: /^products/ }).click()
    await list.getByRole('link', { name: 'Everything about products' }).click()
    const recent = page.getByRole('region', { name: 'Recent questions that used it' })
    await recent.getByRole('link', { name: question }).click()

    await expect(page).toHaveURL(conversation)
    await expect(page.getByRole('article', { name: question })).toBeVisible()
  })
}
