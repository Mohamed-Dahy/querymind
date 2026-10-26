import { setupWorker } from 'msw/browser'

import { handlers } from './handlers'

/**
 * The fake API in the browser (`npm run dev:mock`, and the Playwright smoke test). A service
 * worker (public/mockServiceWorker.js) catches /api requests and answers them from the fixtures.
 */
export function startMockApi() {
  // 'bypass': everything else (Vite's modules, fonts) goes to the network as usual.
  return setupWorker(...handlers).start({ onUnhandledFrame: 'bypass' })
}
