import { defineConfig, devices } from '@playwright/test'

// Its own port, away from `npm run dev` (5173, or 5174 for a second one). A dev server proxies to
// the real API, so testing one by mistake would pass for the wrong reason.
const PORT = 5199
const BASE_URL = `http://localhost:${PORT}`

// The fake API is a service worker (MSW), and Chromium hides the requests it passes on from
// Playwright's routes unless this is on. The loading tests (e2e/loading.spec.ts) hold back a page's
// code with a route, so they need it. Set here, before Playwright starts any browser.
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS ??= '1'

// https://playwright.dev/docs/test-configuration
export default defineConfig({
  testDir: './e2e',
  // Checks wait 15 s, not 5: Vite compiles the answer and chart code the first time a cold server
  // needs it, with several workers at once. A check that is really failing only takes longer to say so.
  expect: { timeout: 15_000 },
  // CI: fail if a stray test.only was committed; retry once, since a cold browser can be slow.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: BASE_URL,
    // Kept only for a failed test: a step-by-step recording to open with `npx playwright show-trace`.
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run dev:mock -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    // Always a fresh mock-mode server: if the port is taken, fail instead of testing what's on it.
    reuseExistingServer: false,
  },
})
