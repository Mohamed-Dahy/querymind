import { lazy, Suspense } from 'react'
import { createBrowserRouter, type RouteObject } from 'react-router'

import { AuthPage } from '@/auth/AuthPage'
import { RequireLogin } from '@/auth/RequireLogin'
import { PageSkeleton } from '@/components/PageSkeleton'
import { AppShell } from '@/layout/AppShell'
import { CanvasPage } from '@/pages/CanvasPage'
import {
  LazyAblationsPage,
  LazyEvalsPage,
  LazyExplorePage,
  LazyGlossaryPage,
  LazyModelsPage,
  LazyQuestionPage,
  LazyQuestionsPage,
  LazyTablePage,
} from '@/pages/LazyPages'
import { NotFoundPage } from '@/pages/NotFoundPage'

/** What a route tells the shell: its page name, shown in the browser tab (useFocusOnRouteChange). */
export type RouteHandle = { title: string }

// The style guide exists only in dev: in a production build `import.meta.env.DEV` is `false`, so the
// bundler drops this import and the page's code never ships.
const Styleguide = import.meta.env.DEV ? lazy(() => import('@/pages/Styleguide')) : null

const styleguideRoutes: RouteObject[] = Styleguide
  ? [
      {
        path: '/styleguide',
        element: (
          <Suspense fallback={<PageSkeleton />}>
            <Styleguide />
          </Suspense>
        ),
      },
    ]
  : []

/**
 * Every screen has a URL, so it can be bookmarked, shared, and reached with the back button.
 * Logins (U7.1): the canvas and the explorer need one (RequireLogin); the evals lab is public on
 * purpose (our own eval data, the portfolio page); /login and /signup are outside the shell.
 */
export const routes: RouteObject[] = [
  { path: '/login', element: <AuthPage mode="login" /> },
  { path: '/signup', element: <AuthPage mode="signup" /> },
  {
    path: '/',
    element: <AppShell />,
    children: [
      {
        index: true,
        element: (
          <RequireLogin>
            <CanvasPage />
          </RequireLogin>
        ),
        handle: { title: 'Canvas' } satisfies RouteHandle,
      },
      {
        path: 'c/:conversationId',
        element: (
          <RequireLogin>
            <CanvasPage />
          </RequireLogin>
        ),
        handle: { title: 'Canvas' } satisfies RouteHandle,
      },
      {
        path: 'explore',
        element: (
          <RequireLogin>
            <LazyExplorePage />
          </RequireLogin>
        ),
        handle: { title: 'Data explorer' } satisfies RouteHandle,
      },
      {
        path: 'explore/glossary',
        element: (
          <RequireLogin>
            <LazyGlossaryPage />
          </RequireLogin>
        ),
        handle: { title: 'Business glossary' } satisfies RouteHandle,
      },
      {
        path: 'explore/tables/:table',
        element: (
          <RequireLogin>
            <LazyTablePage />
          </RequireLogin>
        ),
        handle: { title: 'Data explorer' } satisfies RouteHandle,
      },
      {
        path: 'evals',
        element: <LazyEvalsPage />,
        handle: { title: 'Evals lab' } satisfies RouteHandle,
      },
      {
        path: 'evals/ablations',
        element: <LazyAblationsPage />,
        handle: { title: 'What each part adds' } satisfies RouteHandle,
      },
      {
        path: 'evals/models',
        element: <LazyModelsPage />,
        handle: { title: 'Model comparison' } satisfies RouteHandle,
      },
      {
        path: 'evals/questions',
        element: <LazyQuestionsPage />,
        handle: { title: 'Every question' } satisfies RouteHandle,
      },
      {
        path: 'evals/questions/:id',
        element: <LazyQuestionPage />,
        handle: { title: 'Question' } satisfies RouteHandle,
      },
      // Any other path inside the shell: the 404 page, with the rail still there to get back.
      {
        path: '*',
        element: <NotFoundPage />,
        handle: { title: 'Page not found' } satisfies RouteHandle,
      },
    ],
  },
  ...styleguideRoutes,
]

/** The app's router: real browser URLs (tests use createMemoryRouter with the same routes). */
export function createAppRouter() {
  return createBrowserRouter(routes)
}
