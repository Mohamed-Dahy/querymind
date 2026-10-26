import { defineConfig, devices } from '@playwright/test'

// Its own port, away from `npm run dev` (5173, or 5174 for a second one). A dev server proxies to
// the real API, so testing one by mistake would pass for the wrong reason.
const PORT = 5199
const BASE_URL = `http://localhost:${PORT}`

// https://playwright.dev/docs/test-configuration
export default defineConfig({
  testDir: './e2e',
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
