// Runs before every test file (vite.config.ts → test.setupFiles).
import '@testing-library/jest-dom/vitest' // adds matchers like toBeInTheDocument()
import { cleanup, configure } from '@testing-library/react'
import { toast } from 'sonner'
import { afterAll, afterEach, beforeAll, beforeEach } from 'vitest'

import { resetAuth, setSession } from '../api/session'
import { server } from '../mocks/server'
import { mockSession } from '../mocks/session'

// The fake API (src/mocks/handlers.ts) answers every fetch to /api from the fixtures. 'error': a
// request no handler matches fails the test, instead of quietly trying the real network.
beforeAll(() => server.listen({ onUnhandledFrame: 'error' }))
// A test can swap one endpoint with server.use(...); put the normal handlers back after it.
afterEach(() => server.resetHandlers())
// The fake API keeps its saved conversations in localStorage (mocks/conversations.ts).
afterEach(() => localStorage.clear())
// Sonner keeps its toasts in the module, and a new <Toaster> shows them again: without this, one
// test's toast would appear in the next test too.
afterEach(() => {
  for (const { id } of toast.getToasts()) toast.dismiss(id)
})
afterAll(() => server.close())

// Logged in from the first render (U7.1), as after the page-load refresh: tests about something else
// stay as they were. Logged-out tests call resetAuth() and make the refresh fail (src/api/session.test.ts).
beforeEach(() => setSession(mockSession))
afterEach(() => resetAuth())

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

// jsdom has no ResizeObserver either; Radix's tooltip and popovers measure themselves with it.
// Nothing is laid out in jsdom, so a stand-in that never calls back is enough.
if (typeof window.ResizeObserver !== 'function') {
  window.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

// jsdom has no pointer capture; a toast asks for it the moment one of its buttons is pressed.
HTMLElement.prototype.setPointerCapture ??= () => {}

// jsdom doesn't scroll either; the command palette (cmdk) scrolls the selected item into view.
Element.prototype.scrollIntoView ??= () => {}

// jsdom lays nothing out, so every element is 0 × 0 px, and a virtualized list (ResultTable) would
// draw no rows. Give elements the size of the table's scroll area instead: a window of rows appears,
// as in a browser.
Object.defineProperties(HTMLElement.prototype, {
  clientHeight: { configurable: true, get: () => 384 },
  clientWidth: { configurable: true, get: () => 800 },
})

// findBy… and waitFor wait 5 s, not 1 s: lazy parts (AnswerCell, the chart) are compiled by Vite the
// first time a test file loads them, and the whole suite at once keeps every core busy. A test that
// is really failing only takes longer to say so.
configure({ asyncUtilTimeout: 5000 })

// Unmount what each test rendered, so tests don't see each other's DOM.
afterEach(() => cleanup())
