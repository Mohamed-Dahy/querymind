import { AxeBuilder } from '@axe-core/playwright'
import type { Locator, Page } from '@playwright/test'

import { expect, test } from './fixtures.ts'
import { apiStatusLoaded, ROUTES, THEMES, useTheme } from './helpers.ts'

// U6.4: the accessibility checks that belong to the whole app, not to one page. axe itself runs
// after every test (fixtures.ts); these cover what axe cannot see: the keyboard, focus, motion and
// zoom. The hand checklist that goes with them is in docs/ui-design-system.md §7.

const EXAMPLE = 'What is the total sales amount per category?'

/** Press Tab (or Shift+Tab) until `target` has focus; fail if it is not reached in `max` presses. */
async function tabTo(page: Page, target: Locator, { back = false, max = 40 } = {}) {
  for (let i = 0; i < max; i++) {
    if (await hasFocus(target)) return
    await page.keyboard.press(back ? 'Shift+Tab' : 'Tab')
  }
  await expect(target, `not reached with ${max} Tab presses`).toBeFocused()
}

/** Whether `target` is the element with focus now (no waiting, unlike toBeFocused). */
async function hasFocus(target: Locator) {
  return (await target.and(target.page().locator(':focus')).count()) > 0
}

/** Whether focus is on `area` or anywhere inside it. */
async function focusInside(area: Locator) {
  return (await area.locator(':scope:focus, :focus').count()) > 0
}

/**
 * What has focus now: a short name, and whether something shows it (an outline or a ring). The
 * code runs in the browser, so it is a string, like in the other e2e files: their TypeScript setup
 * has no browser types (no `document`), on purpose.
 */
async function focusedStop(page: Page) {
  return (await page.evaluate(`(() => {
    const el = document.activeElement
    if (!el || el === document.body) return null
    const style = getComputedStyle(el)
    const outline = style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0
    // shadcn's components draw their ring with box-shadow (Tailwind's ring-*), not an outline.
    const ring = style.boxShadow !== 'none'
    const label = el.getAttribute('aria-label') ?? el.textContent.trim().slice(0, 40)
    return { name: el.tagName.toLowerCase() + ' "' + label + '"', shown: outline || ring }
  })()`)) as { name: string; shown: boolean } | null
}

test('keyboard only: ask, look at the answer, open and close the drawer, start again', async ({
  page,
}) => {
  await page.goto('/')
  await apiStatusLoaded(page)

  // The first Tab is the skip link; Enter jumps past the top bar and the menu.
  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()

  // An example question, asked with Enter.
  await tabTo(page, page.getByRole('button', { name: EXAMPLE }))
  await page.keyboard.press('Enter')
  const answer = page.getByRole('article', { name: EXAMPLE })
  await expect(answer.getByRole('tab', { name: 'Chart' })).toBeVisible()

  // The answer's tabs: arrow keys move between them (the tab pattern), and the table shows.
  await tabTo(page, answer.getByRole('tab', { selected: true }))
  await page.keyboard.press('ArrowRight')
  await expect(answer.getByRole('tab', { name: 'Table' })).toBeFocused()
  await expect(answer.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true')
  // The next Tab stop is the panel itself (Radix makes it one), and it shows a ring too.
  await page.keyboard.press('Tab')
  expect(await focusedStop(page)).toMatchObject({ shown: true })

  // The drawer: Enter opens it with focus inside, Escape closes it and gives focus back.
  const opener = answer.getByRole('button', { name: 'Show your work' })
  await tabTo(page, opener)
  await page.keyboard.press('Enter')
  const drawer = page.getByRole('dialog')
  await expect(drawer).toBeVisible()
  expect(await focusInside(drawer)).toBe(true)
  // Focus stays inside while it is open: Tab many times, and it never lands behind the drawer.
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press('Tab')
    expect(await focusInside(drawer)).toBe(true)
  }
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await expect(opener).toBeFocused()

  // Back up to the top bar (Shift+Tab) and start a new conversation. Like every page change,
  // focus moves to the page (useFocusOnRouteChange), and the composer is the next stops away.
  await tabTo(page, page.getByRole('banner').getByRole('button', { name: 'New conversation' }), {
    back: true,
  })
  await page.keyboard.press('Enter')
  await expect(page.getByRole('main')).toBeFocused()
  const box = page.getByRole('textbox', { name: 'Your question' })
  await tabTo(page, box)

  // A question of your own, sent from the keyboard.
  await page.keyboard.type('How many orders were shipped late?')
  await page.keyboard.press('Control+Enter')
  await expect(page.getByRole('article', { name: /^How many orders/ })).toBeVisible()
})

test('keyboard only: the history sheet and the page menu', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 })
  await page.goto('/')
  await apiStatusLoaded(page)

  // On a phone the history is a sheet: it opens from the menu, and Escape gives focus back.
  const history = page.getByRole('navigation', { name: 'Main' }).getByRole('button', {
    name: 'History',
  })
  await tabTo(page, history)
  await page.keyboard.press('Enter')
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toBeHidden()
  await expect(history).toBeFocused()

  // Every page in the menu opens with Enter, and focus moves to the new page.
  const evals = page.getByRole('navigation', { name: 'Main' }).getByRole('link', { name: 'Evals' })
  await tabTo(page, evals, { back: true })
  await page.keyboard.press('Enter')
  await expect(page.getByRole('heading', { level: 1, name: 'Evals lab' })).toBeVisible()
  await expect(page.getByRole('main')).toBeFocused()
  await expect(evals).toHaveAttribute('aria-current', 'page')
})

// WCAG 2.4.7 "Focus visible": wherever Tab lands, something shows it. axe does not check this.
// Every stop of the page, first to last: the cap only stops a runaway loop.
const MAX_STOPS = 400

for (const { path, heading } of ROUTES) {
  test(`${path}: every Tab stop shows a focus ring`, async ({ page }) => {
    test.slow() // a long page has 100+ stops
    await page.goto(path)
    await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
    if (path !== '/styleguide') await apiStatusLoaded(page)

    // Tab until focus leaves the page after its last stop (the browser then reports <body>).
    let stops = 0
    for (; stops < MAX_STOPS; stops++) {
      await page.keyboard.press('Tab')
      const stop = await focusedStop(page)
      if (!stop) break
      expect(stop.shown, `${stop.name} has focus but no visible ring`).toBe(true)
    }
    expect(stops, 'more Tab stops than MAX_STOPS: raise it').toBeLessThan(MAX_STOPS)
    expect(stops).toBeGreaterThan(3)
  })
}

test.describe('reduced motion', () => {
  test.beforeEach(async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
  })

  test('nothing slides or fades: skeletons, the drawer and the palette appear at once', async ({
    page,
  }) => {
    // Slow replies keep the skeletons on screen long enough to look at them.
    await page.addInitScript(() => localStorage.setItem('querymind-mock-delay', '1500'))
    await page.goto('/evals')
    await expect(page.locator('[data-slot="skeleton"]').first()).toBeVisible()
    expect(await longAnimations(page)).toEqual([])

    await page.goto('/')
    await apiStatusLoaded(page)
    await page.getByRole('button', { name: EXAMPLE }).click()
    await page.getByRole('button', { name: 'Show your work' }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    expect(await longAnimations(page)).toEqual([])
    await page.keyboard.press('Escape')

    await page.keyboard.press('Control+k')
    await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible()
    expect(await longAnimations(page)).toEqual([])
  })

  test('the chart is drawn at its full size at once (Recharts does not grow the bars)', async ({
    page,
  }) => {
    // Recharts grows bars with JavaScript, frame by frame, by rewriting each bar's shape (its `d`
    // attribute), so the CSS rule in index.css cannot stop it. Count those rewrites: none means
    // the bars were drawn once, at their final size. (With motion allowed there are dozens.)
    await page.addInitScript(`
      window.barRedraws = 0
      new MutationObserver((changes) => {
        for (const change of changes)
          if (change.target.closest?.('.recharts-bar-rectangle')) window.barRedraws++
      }).observe(document, { subtree: true, attributes: true, attributeFilter: ['d'] })
    `)
    await page.goto('/')
    await apiStatusLoaded(page)
    await page.getByRole('button', { name: EXAMPLE }).click()
    await expect(page.locator('.recharts-bar-rectangle path').first()).toBeVisible()
    await page.waitForTimeout(1_000) // longer than Recharts' grow-in
    expect(await page.evaluate('window.barRedraws')).toBe(0)
  })
})

/** Every animation or transition running now that lasts longer than 1 ms (reduced motion: none). */
async function longAnimations(page: Page) {
  return (await page.evaluate(`document
    .getAnimations()
    .map((a) => ({
      name: a.animationName ?? a.transitionProperty ?? 'animation',
      ms: Number(a.effect?.getTiming().duration ?? 0),
    }))
    .filter((a) => a.ms > 1)`)) as { name: string; ms: number }[]
}

// WCAG 1.4.4 and 1.4.10: zoom to 200 % on a 1280×800 laptop is a 640×400 page in CSS pixels, and
// 400 % (the reflow rule) is 320 px wide. Playwright has no zoom button, so a smaller window
// stands in for it: the layout sees exactly the same width.
const ZOOMS = [
  { label: '200 % zoom', viewport: { width: 640, height: 400 } },
  { label: '400 % zoom (320 px reflow)', viewport: { width: 320, height: 800 } },
]

for (const { label, viewport } of ZOOMS) {
  test.describe(label, () => {
    test.use({ viewport })

    for (const { path, heading } of ROUTES) {
      test(`${path} reads without scrolling sideways`, async ({ page }) => {
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
        const sideways = (await page.evaluate(
          'document.documentElement.scrollWidth - document.documentElement.clientWidth',
        )) as number
        expect(sideways).toBeLessThanOrEqual(0)
      })
    }

    test('a question can still be asked and its answer read', async ({ page }) => {
      await page.goto('/')
      await apiStatusLoaded(page)
      await page.getByRole('button', { name: EXAMPLE }).click()
      const answer = page.getByRole('article', { name: EXAMPLE })
      await expect(answer.getByRole('tab', { name: 'Chart' })).toBeVisible()
      await answer.getByRole('tab', { name: 'Table' }).click()
      await expect(answer.getByRole('table')).toBeVisible()
      await answer.getByRole('tab', { name: 'SQL' }).click()
      await expect(answer.getByRole('tabpanel')).toContainText('SELECT')
      // The composer is still there to ask the next one.
      await expect(page.getByRole('textbox', { name: 'Your question' })).toBeInViewport()
      // The drawer opens last: axe checks it, at this size, when the test ends (fixtures.ts).
      await answer.getByRole('button', { name: 'Show your work' }).click()
      await expect(page.getByRole('dialog')).toBeVisible()
    })
  })
}

// Contrast on the canvas. The dot grid is a CSS gradient, and axe cannot measure text on a
// gradient: it files every such text as "incomplete", not as a pass or a failure, so the axe check
// after each test never saw it. Here the dots are hidden (they are 6–7 % see-through, so the
// colour behind the text is --bg), and axe must then measure, and pass, every text, in both themes.
//
// Except three cases axe can never measure, dots or not (its own reason codes): text that another
// element overlaps (the explorer graph's nodes), text partly hidden by its scroll box (the long
// table list), and SVG text (chart axes). Their text uses only colour tokens, and
// src/styles/contrast.test.ts checks every token pair. Any other reason fails the test.
const CANT_MEASURE = new Set(['bgOverlap', 'elmPartiallyObscured', 'imgNode'])

for (const theme of THEMES) {
  test.describe(`${theme} theme: axe measures, and passes, the contrast of text`, () => {
    test.beforeEach(async ({ page }) => {
      await useTheme(page, theme)
    })

    for (const { path, heading } of ROUTES) {
      test(`on ${path}`, async ({ page }) => {
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
        if (path !== '/styleguide') await apiStatusLoaded(page)
        await page.addStyleTag({ content: '.canvas-grid { background-image: none !important; }' })

        const results = await new AxeBuilder({ page }).withRules(['color-contrast']).analyze()
        const failed = results.violations.flatMap((v) => v.nodes.map((n) => n.target.join(' ')))
        const unmeasured = results.incomplete
          .flatMap((v) => v.nodes)
          .flatMap((n) =>
            n.any
              .map((check) => (check.data as { messageKey?: string } | null)?.messageKey ?? '')
              .filter((reason) => !CANT_MEASURE.has(reason))
              .map((reason) => `${n.target.join(' ')} (${reason || 'no reason given'})`),
          )
        expect({ failed, unmeasured }).toEqual({ failed: [], unmeasured: [] })
      })
    }
  })
}
