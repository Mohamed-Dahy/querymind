// Runs before every test file (vite.config.ts → test.setupFiles).
import '@testing-library/jest-dom/vitest' // adds matchers like toBeInTheDocument()
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'

import { server } from '../mocks/server'

// The fake API (src/mocks/handlers.ts) answers every fetch to /api from the fixtures. 'error': a
// request no handler matches fails the test, instead of quietly trying the real network.
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
// A test can swap one endpoint with server.use(...); put the normal handlers back after it.
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

// jsdom has no matchMedia (the theme code uses it). This stand-in says "no preference" for every query;
// a test can stub its own (theme.test.ts). Assigned once, so `unstubGlobals` doesn't remove it.
if (typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })
}

// Unmount what each test rendered, so tests don't see each other's DOM.
afterEach(() => cleanup())
