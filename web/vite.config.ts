import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Where uvicorn runs in dev (`uv run uvicorn querymind.api.main:app --reload`).
const API_TARGET = 'http://127.0.0.1:8000'

// The browser calls `/api/health`; the proxy forwards it to `http://127.0.0.1:8000/health`.
// One origin for the browser, so FastAPI needs no CORS setup (ADR 008).
const apiProxy = {
  '/api': {
    target: API_TARGET,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { proxy: apiProxy },
  preview: { proxy: apiProxy },
  test: {
    environment: 'jsdom',
    // Only unit and component tests; the browser tests in e2e/ are Playwright's (npm run e2e).
    include: ['src/**/*.test.{ts,tsx}'],
    setupFiles: ['./src/test/setup.ts'],
    // CSS is skipped in tests by default (imports come back empty); contrast.test.ts reads the tokens.
    css: { include: [/tokens\.css/] },
    // Each test starts with the real `fetch` again, even if a test stubbed it.
    unstubGlobals: true,
  },
})
