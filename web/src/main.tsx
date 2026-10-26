import { QueryClientProvider } from '@tanstack/react-query'
import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'

import { createQueryClient } from './api/queryClient.ts'
import App from './App.tsx'
import { Toaster } from './components/ui/sonner.tsx'
import { TooltipProvider } from './components/ui/tooltip.tsx'
import './index.css'
import { initTheme } from './lib/theme.ts'

const root = document.getElementById('root')
if (!root) {
  throw new Error('index.html has no #root element')
}

// Mock mode (`npm run dev:mock`): a service worker answers /api from the recorded fixtures, so the
// UI runs with no API, database or LLM. Started before the first render, so even the first health
// check is answered. In every other mode the bundler drops this import: MSW never ships.
if (import.meta.env.MODE === 'mock') {
  const { startMockApi } = await import('./mocks/browser.ts')
  await startMockApi()
}

initTheme()
const queryClient = createQueryClient()

// The style guide exists only in dev: in a production build `import.meta.env.DEV` is `false`, so the
// bundler drops this import and the page's code never ships. Open it at /?styleguide.
const Styleguide = import.meta.env.DEV ? lazy(() => import('./pages/Styleguide.tsx')) : null
const wantsStyleguide = new URLSearchParams(window.location.search).has('styleguide')

// StrictMode runs effects twice in dev, to surface missing cleanups early.
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        {Styleguide && wantsStyleguide ? (
          <Suspense fallback={null}>
            <Styleguide />
          </Suspense>
        ) : (
          <App />
        )}
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
