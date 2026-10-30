import { QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'

import { createQueryClient } from './api/queryClient.ts'
import { Toaster } from './components/ui/sonner.tsx'
import { TooltipProvider } from './components/ui/tooltip.tsx'
import './index.css'
import { initTheme } from './lib/theme.ts'
import { createAppRouter } from './router.tsx'

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
// Every page has a URL (router.tsx); in dev the style guide is at /styleguide.
const router = createAppRouter()

// StrictMode runs effects twice in dev, to surface missing cleanups early.
createRoot(root).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <RouterProvider router={router} />
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
