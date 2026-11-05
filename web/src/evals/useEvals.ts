import { useQuery } from '@tanstack/react-query'

import { getEvals } from '@/api/evals'

/**
 * The saved eval results. The server reads them once from a file, so there's no need to ask again
 * soon. A failure is a missing file, which a retry can't fix, and the page has a Try again button.
 */
export function useEvals() {
  return useQuery({
    queryKey: ['evals'],
    queryFn: ({ signal }) => getEvals(signal),
    staleTime: 5 * 60_000,
    retry: false,
  })
}
