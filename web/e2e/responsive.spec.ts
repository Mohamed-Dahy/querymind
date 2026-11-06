import { expect, test, type Page, type TestInfo } from '@playwright/test'

import { apiStatusLoaded, ROUTES } from './helpers.ts'

// U6.3: every screen at the four design widths (phone, tablet, laptop, desktop). The page itself
// never scrolls sideways; wide tables scroll inside their own box. Screenshots are saved with the
// test's output to look at, not compared pixel by pixel: fonts render differently on Windows and
// on CI's Linux, so a pixel baseline would fail for no real reason.

const WIDTHS = [375, 768, 1024, 1440] as const

async function sidewaysScroll(page: Page): Promise<number> {
  return (await page.evaluate(
    'document.documentElement.scrollWidth - document.documentElement.clientWidth',
  )) as number
}

async function keepScreenshot(page: Page, testInfo: TestInfo, name: string) {
  await testInfo.attach(name, {
    body: await page.screenshot({ fullPage: true }),
    contentType: 'image/png',
  })
}

for (const width of WIDTHS) {
  test.describe(`${width} px wide`, () => {
    test.use({ viewport: { width, height: 800 } })

    test('no route scrolls sideways', async ({ page }, testInfo) => {
      for (const { path, heading } of ROUTES) {
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
        expect(await sidewaysScroll(page), path).toBeLessThanOrEqual(0)
        await keepScreenshot(page, testInfo, `${width}${path.replaceAll('/', '_')}`)
      }
    })

    test('an answer, and its drawer, fit without scrolling sideways', async ({
      page,
    }, testInfo) => {
      await page.goto('/')
      await apiStatusLoaded(page)
      await page
        .getByRole('button', { name: 'What is the total sales amount per category?' })
        .click()
      await expect(page.getByRole('button', { name: 'Show your work' })).toBeVisible()
      expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0)
      await keepScreenshot(page, testInfo, `${width}-answer`)

      await page.getByRole('button', { name: 'Show your work' }).click()
      const drawer = page.getByRole('dialog', { name: 'How this answer was made' })
      await expect(drawer).toBeVisible()
      expect(await sidewaysScroll(page)).toBeLessThanOrEqual(0)
      await keepScreenshot(page, testInfo, `${width}-drawer`)
    })
  })
}

test.describe('on a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('the top bar and menu leave most of the screen to the page', async ({ page }) => {
    await page.goto('/')
    await apiStatusLoaded(page)
    const header = await page.getByRole('banner').boundingBox()
    const menu = await page.getByRole('navigation', { name: 'Main' }).boundingBox()
    // Was 282 px (173 + 109) of an 800 px screen before U6.3.
    expect((header?.height ?? 0) + (menu?.height ?? 0)).toBeLessThanOrEqual(170)
  })

  test('the buttons keep their names for screen readers when their words are hidden', async ({
    page,
  }) => {
    await page.goto('/')
    const bar = page.getByRole('banner')
    for (const name of ['Search', 'New conversation', 'Dark', 'Light', 'System']) {
      await expect(bar.getByRole('button', { name })).toBeVisible()
    }
    await expect(bar.getByText('Northwind + Chinook')).toBeHidden()
  })

  test('the drawer rises from the bottom, leaving the top of the answer in view', async ({
    page,
  }) => {
    // No slide-in, so the drawer is measured where it stops.
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await page.goto('/')
    await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()
    await page.getByRole('button', { name: 'Show your work' }).click()

    const drawer = page.getByRole('dialog', { name: 'How this answer was made' })
    await expect(drawer).toHaveAttribute('data-side', 'bottom')
    const box = await drawer.boundingBox()
    expect(box?.y ?? 0).toBeGreaterThan(0)
    expect((box?.y ?? 0) + (box?.height ?? 0)).toBeCloseTo(800, 0)

    // Closing it gives the focus back to the button, as on a desktop.
    await page.keyboard.press('Escape')
    await expect(page.getByRole('button', { name: 'Show your work' })).toBeFocused()
  })
})

test('on a desktop the drawer is a side panel', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.goto('/')
  await page.getByRole('button', { name: 'What is the total sales amount per category?' }).click()
  await page.getByRole('button', { name: 'Show your work' }).click()
  await expect(page.getByRole('dialog', { name: 'How this answer was made' })).toHaveAttribute(
    'data-side',
    'right',
  )
})
