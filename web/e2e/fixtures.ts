import { expect, test as base } from '@playwright/test'

import { a11yIssues } from './helpers.ts'

// U6.4: axe checks the page at the end of EVERY test, so each state a test reaches (an answer, a
// filter, a phone layout, a slow load) is also checked for accessibility, not only the few states
// a test checks by hand. The spec files import `test` and `expect` from here, not from Playwright.

type Options = {
  /** Set to false with `test.use({ a11yAfterEach: false })`, with a comment saying why. */
  a11yAfterEach: boolean
}

export const test = base.extend<Options & { a11yCheck: void }>({
  a11yAfterEach: [true, { option: true }],

  // `auto`: every test gets this fixture without asking for it. The code after `use()` runs once
  // the test's own steps are done, while its page is still open.
  a11yCheck: [
    async ({ page, a11yAfterEach }, use, testInfo) => {
      await use()
      // A test that already failed keeps its own error: an axe report on top would hide it.
      if (!a11yAfterEach || testInfo.status !== testInfo.expectedStatus) return
      if (page.isClosed() || page.url() === 'about:blank') return
      expect(await a11yIssues(page), 'axe, at the end of the test').toEqual([])
    },
    { auto: true },
  ],
})

export { expect }
