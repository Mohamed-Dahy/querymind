import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import type { Plugin } from 'vite'
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

// Fonts to download alongside the JS instead of after it. Without a preload the browser only finds a
// font once the CSS has loaded and the page has rendered. Only Fira Sans 400 (latin), the body text
// and the canvas's largest text: on a slow phone every preload shares bandwidth with the JS, and
// preloading all four first-screen fonts made the page slower (docs/lighthouse/README.md). The other
// weights, Fira Code and other alphabets still load on demand, only if a page uses them.
const PRELOAD_FONTS = [/^assets\/fira-sans-latin-400-normal-[\w-]+\.woff2$/]

// Adds a <link rel="preload"> to dist/index.html for each font above. Build only: the dev server
// has no hashed file names to point at. The file names change with each font release, so they are
// read from the finished bundle; a pattern that matches nothing fails the build instead of quietly
// preloading nothing.
function preloadFonts(): Plugin {
  let base = '/'
  return {
    name: 'querymind:preload-fonts',
    apply: 'build',
    configResolved(config) {
      base = config.base
    },
    transformIndexHtml: {
      order: 'post',
      handler(_html, ctx) {
        const files = Object.keys(ctx.bundle ?? {})
        return PRELOAD_FONTS.map((pattern) => {
          const file = files.find((name) => pattern.test(name))
          if (!file) {
            throw new Error(`preloadFonts: no font file matches ${pattern}; check index.css`)
          }
          return {
            tag: 'link',
            // `crossorigin` is required for fonts: without it, the preload is not reused and the
            // font downloads twice.
            attrs: {
              rel: 'preload',
              as: 'font',
              type: 'font/woff2',
              href: base + file,
              crossorigin: '',
            },
            injectTo: 'head' as const,
          }
        })
      },
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), preloadFonts()],
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
    // The first test to reach a lazy part (AnswerCell, the chart) also waits for Vite to compile it.
    testTimeout: 15_000,
    // Each test starts with the real `fetch` again, even if a test stubbed it.
    unstubGlobals: true,
  },
})
