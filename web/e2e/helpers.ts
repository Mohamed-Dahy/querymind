import { AxeBuilder } from '@axe-core/playwright'
import { expect, type Page } from '@playwright/test'

import openapi from '../openapi.json' with { type: 'json' }

// Shared by the e2e files. The app runs in a real browser against the mock API (playwright.config.ts).
export const VERSION = openapi.info.version
export const THEMES = ['dark', 'light'] as const

/**
 * Every route, with its page heading and tab title, and `code`: the lazy file the page downloads
 * on its own (a test holds it back to see the loading state). /styleguide exists because the tests
 * run on a dev server.
 */
export const ROUTES: { path: string; heading: string; title: string; code?: string }[] = [
  { path: '/', heading: 'Ask about your data', title: 'Canvas · QueryMind' },
  {
    path: '/c/3f2a9c1e-0000-4000-8000-000000000000',
    heading: 'Ask about your data',
    title: 'Canvas · QueryMind',
  },
  {
    path: '/explore',
    heading: 'Data explorer',
    title: 'Data explorer · QueryMind',
    code: 'src/pages/ExplorePage.tsx',
  },
  {
    path: '/explore/glossary',
    heading: 'Business glossary',
    title: 'Business glossary · QueryMind',
    code: 'src/explorer/GlossaryPage.tsx',
  },
  {
    path: '/explore/tables/orders',
    heading: 'orders',
    title: 'Data explorer · QueryMind',
    code: 'src/explorer/TablePage.tsx',
  },
  {
    path: '/evals',
    heading: 'Evals lab',
    title: 'Evals lab · QueryMind',
    code: 'src/pages/EvalsPage.tsx',
  },
  {
    path: '/evals/ablations',
    heading: 'What each part adds',
    title: 'What each part adds · QueryMind',
    code: 'src/evals/AblationsPage.tsx',
  },
  {
    path: '/evals/models',
    heading: 'Model comparison',
    title: 'Model comparison · QueryMind',
    code: 'src/evals/ModelsPage.tsx',
  },
  {
    path: '/evals/questions',
    heading: 'Every question',
    title: 'Every question · QueryMind',
    code: 'src/evals/QuestionsPage.tsx',
  },
  {
    path: '/evals/questions/b01_key_account_count',
    heading: 'Question b01_key_account_count',
    title: 'Question · QueryMind',
    code: 'src/evals/QuestionPage.tsx',
  },
  { path: '/no/such/page', heading: 'Page not found', title: 'Page not found · QueryMind' },
  {
    path: '/styleguide',
    heading: 'QueryMind · style guide',
    title: 'QueryMind',
    code: 'src/pages/Styleguide.tsx',
  },
]

/**
 * Every problem axe finds under WCAG 2.1 A and AA, at every impact level (minor and moderate
 * too, not only serious and critical: U6.4).
 */
export async function a11yIssues(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  // Just the rule, its impact and the elements, so a failure says what to fix.
  return results.violations.map((v) => ({
    rule: v.id,
    impact: v.impact,
    help: v.help,
    nodes: v.nodes.map((n) => n.target.join(' ')),
  }))
}

/** The shell's health line has loaded (so checks see the page people see, not the spinner). */
export async function apiStatusLoaded(page: Page) {
  // In the top bar: a page can have status lines of its own (the question matrix's count).
  await expect(page.getByRole('banner').getByRole('status')).toHaveText(
    `API ok · version ${VERSION}`,
  )
}

/** Open the page with a saved theme, like a returning user's choice (index.html applies it first). */
export async function useTheme(page: Page, theme: (typeof THEMES)[number]) {
  await page.addInitScript((choice) => localStorage.setItem('querymind-theme', choice), theme)
}
