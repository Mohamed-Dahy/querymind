import { lazy, Suspense } from 'react'

// Their own download: most visits are to the canvas, and these pages have a lot of their own.
const ExplorePage = lazy(() => import('./ExplorePage'))

export function LazyExplorePage() {
  return (
    <Suspense fallback={null}>
      <ExplorePage />
    </Suspense>
  )
}
