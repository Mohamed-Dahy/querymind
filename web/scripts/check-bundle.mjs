// Bundle budget (UI U6.5): the JavaScript a first visit must download before the app can start
// stays under BUDGET_KB, gzipped (what travels over the network). Run after `npm run build`:
// `npm run size`. CI runs it after its build step, so large growth of the first load fails the job
// instead of quietly slowing the canvas.
import { readFileSync } from 'node:fs'
import { gzipSync } from 'node:zlib'

// The plan's limit (plan_ui.md U6.5). The first load was 153 kB on 2026-11-06, so this catches large
// growth, not every slip: Shiki (62 kB) or React Flow (56 kB) moved into the first load would still
// pass (about 215 kB). Recharts with its colour hook (96 kB) would fail.
const BUDGET_KB = 250
const DIST = new URL('../dist/', import.meta.url)

// The entry <script src> plus every <link rel="modulepreload">: Vite writes one modulepreload link
// per file the entry imports directly or indirectly, so together they are everything the browser
// fetches before the first render. Lazy chunks (explorer, evals, the chart, Shiki) are not in
// index.html, so they don't count; they load only when a page needs them.
const FIRST_LOAD_JS =
  /<(?:script\b[^>]*\bsrc|link\b[^>]*\brel="modulepreload"[^>]*\bhref)="\/([^"]+\.js)"/g

let html
try {
  html = readFileSync(new URL('index.html', DIST), 'utf8')
} catch {
  console.error('dist/index.html not found: run `npm run build` first.')
  process.exit(1)
}

const files = [...html.matchAll(FIRST_LOAD_JS)].map((match) => match[1])
if (files.length === 0) {
  // A budget that counts nothing always passes; fail loudly if Vite changes how it writes the HTML.
  console.error('No <script src> or modulepreload links found in dist/index.html.')
  process.exit(1)
}

// kB = 1000 bytes, the unit `vite build` prints, so the numbers can be compared with its report.
const sizes = files.map((file) => ({
  file,
  kb: gzipSync(readFileSync(new URL(file, DIST))).length / 1000,
}))
const total = sizes.reduce((sum, { kb }) => sum + kb, 0)

for (const { file, kb } of sizes.toSorted((a, b) => b.kb - a.kb)) {
  console.log(`${kb.toFixed(1).padStart(8)} kB  ${file}`)
}
console.log(`${total.toFixed(1).padStart(8)} kB  first-load JS, gzip (budget ${BUDGET_KB} kB)`)

if (total > BUDGET_KB) {
  console.error(`Over budget by ${(total - BUDGET_KB).toFixed(1)} kB: lazy-load what the first screen
doesn't need (React.lazy + import(), see src/pages/LazyPages.tsx).`)
  process.exit(1)
}
