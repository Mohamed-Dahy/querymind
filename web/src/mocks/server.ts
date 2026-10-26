import { setupServer } from 'msw/node'

import { handlers } from './handlers'

/** The fake API for Vitest (Node has no service worker; MSW patches fetch instead). */
export const server = setupServer(...handlers)
