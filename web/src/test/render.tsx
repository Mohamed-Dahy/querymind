import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Toaster } from '@/components/ui/sonner'
import { MotionProvider } from '@/motion'
import { routes } from '@/router'

/**
 * Render inside a fresh query cache, so tests never share data, and failures aren't retried.
 * Returns the cache too, so a test can ask again now instead of waiting for a poll.
 * Inside the app's MotionProvider too, as in main.tsx (animations are off in tests: setup.ts).
 */
export function renderWithQueryClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return {
    ...render(
      <QueryClientProvider client={client}>
        <MotionProvider>{ui}</MotionProvider>
      </QueryClientProvider>,
    ),
    client,
  }
}

/**
 * The whole app at `path`, with the real routes but an in-memory history (no browser URL bar).
 * Returns the router too, so a test can check `router.state.location` or go back.
 */
export function renderApp(path = '/') {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const app = (
    <>
      <RouterProvider router={router} />
      <Toaster />
    </>
  )
  return { ...renderWithQueryClient(app), router }
}
