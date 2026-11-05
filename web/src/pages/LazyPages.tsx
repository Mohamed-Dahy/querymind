import { lazy, Suspense, type ReactNode } from 'react'

import { PageSkeleton } from '@/components/PageSkeleton'

// Their own download: most visits are to the canvas, and these pages have a lot of their own.
const ExplorePage = lazy(() => import('./ExplorePage'))
const TablePage = lazy(() => import('@/explorer/TablePage'))
const GlossaryPage = lazy(() => import('@/explorer/GlossaryPage'))
const EvalsPage = lazy(() => import('./EvalsPage'))
const AblationsPage = lazy(() => import('@/evals/AblationsPage'))
const ModelsPage = lazy(() => import('@/evals/ModelsPage'))
const QuestionsPage = lazy(() => import('@/evals/QuestionsPage'))
const QuestionPage = lazy(() => import('@/evals/QuestionPage'))

/** While a page's code downloads, the page's shape shows, not an empty screen. */
function Page({ children }: { children: ReactNode }) {
  return <Suspense fallback={<PageSkeleton />}>{children}</Suspense>
}

export function LazyExplorePage() {
  return (
    <Page>
      <ExplorePage />
    </Page>
  )
}

export function LazyTablePage() {
  return (
    <Page>
      <TablePage />
    </Page>
  )
}

export function LazyEvalsPage() {
  return (
    <Page>
      <EvalsPage />
    </Page>
  )
}

export function LazyAblationsPage() {
  return (
    <Page>
      <AblationsPage />
    </Page>
  )
}

export function LazyModelsPage() {
  return (
    <Page>
      <ModelsPage />
    </Page>
  )
}

export function LazyQuestionsPage() {
  return (
    <Page>
      <QuestionsPage />
    </Page>
  )
}

export function LazyQuestionPage() {
  return (
    <Page>
      <QuestionPage />
    </Page>
  )
}

export function LazyGlossaryPage() {
  return (
    <Page>
      <GlossaryPage />
    </Page>
  )
}
