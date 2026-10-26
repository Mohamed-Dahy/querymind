import { QueryClient } from '@tanstack/react-query'

import { ApiError } from './client'

// Errors worth one more try: the API or the network hiccupped. Anything else (unsafe SQL, a bad
// request…) would fail the same way again.
const TRANSIENT: readonly string[] = ['api_unreachable', 'api_timeout', 'db_unavailable']

/** Retry a failed *read* (health, later history and catalog) once, and only for transient errors. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  return failureCount < 1 && error instanceof ApiError && TRANSIENT.includes(error.code)
}

/** One cache of server data for the whole app (provided in main.tsx). */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        // Data stays "fresh" for 30 s: switching tabs or remounting doesn't refetch it.
        staleTime: 30_000,
        refetchOnWindowFocus: false,
      },
      // Asking (a mutation) is never retried automatically: every try spends LLM quota.
      mutations: { retry: false },
    },
  })
}
