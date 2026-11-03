import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'

import { Toaster } from '@/components/ui/sonner'
import { routes } from '@/router'

/** Render inside a fresh query cache, so tests never share data, and failures aren't retried. */
export function renderWithQueryClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
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
