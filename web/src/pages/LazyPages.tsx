import { lazy, Suspense } from 'react'

// Their own download: most visits are to the canvas, and these pages have a lot of their own.
const ExplorePage = lazy(() => import('./ExplorePage'))
const TablePage = lazy(() => import('@/explorer/TablePage'))
const GlossaryPage = lazy(() => import('@/explorer/GlossaryPage'))
const EvalsPage = lazy(() => import('./EvalsPage'))
const AblationsPage = lazy(() => import('@/evals/AblationsPage'))
const ModelsPage = lazy(() => import('@/evals/ModelsPage'))

export function LazyExplorePage() {
  return (
    <Suspense fallback={null}>
      <ExplorePage />
    </Suspense>
  )
}

export function LazyTablePage() {
  return (
    <Suspense fallback={null}>
      <TablePage />
    </Suspense>
  )
}

export function LazyEvalsPage() {
  return (
    <Suspense fallback={null}>
      <EvalsPage />
    </Suspense>
  )
}

export function LazyAblationsPage() {
  return (
    <Suspense fallback={null}>
      <AblationsPage />
    </Suspense>
  )
}

export function LazyModelsPage() {
  return (
    <Suspense fallback={null}>
      <ModelsPage />
    </Suspense>
  )
}

export function LazyGlossaryPage() {
  return (
    <Suspense fallback={null}>
      <GlossaryPage />
    </Suspense>
  )
}
