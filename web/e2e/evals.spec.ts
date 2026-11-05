import { expect, test } from '@playwright/test'

import { seriousA11yIssues, THEMES, useTheme } from './helpers.ts'

test.describe('the scoreboard', () => {
  for (const theme of THEMES) {
    test(`${theme} theme: no serious accessibility problem`, async ({ page }) => {
      await useTheme(page, theme)
      await page.goto('/evals')
      await expect(page.getByRole('listitem', { name: 'Golden 58' })).toBeVisible()

      expect(await seriousA11yIssues(page)).toEqual([])
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
