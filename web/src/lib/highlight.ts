/**
 * SQL syntax highlighting with Shiki (the highlighter VS Code's grammars come from).
 *
 * Only loaded when a SQL tab opens (SqlView imports this file lazily), so it stays out of the
 * first page load. We take the small parts: the core, the SQL grammar, and the JavaScript regex
 * engine (no WebAssembly). The colours are CSS variables (--shiki-token-keyword…) that
 * tokens.css points at our own tokens, so one setup serves both themes.
 */
import {
  createCssVariablesTheme,
  createHighlighterCore,
  type HighlighterCore,
  type ThemedToken,
} from 'shiki/core'
import { createJavaScriptRegexEngine } from 'shiki/engine/javascript'
import sql from 'shiki/langs/sql.mjs'

const THEME = createCssVariablesTheme({ name: 'querymind', variablePrefix: '--shiki-' })

// Created once, on first use, and shared by every SQL view on the page.
let highlighter: Promise<HighlighterCore> | undefined

function getHighlighter(): Promise<HighlighterCore> {
  highlighter ??= createHighlighterCore({
    themes: [THEME],
    langs: [sql],
    engine: createJavaScriptRegexEngine(),
  })
  return highlighter
}

/**
 * The SQL split into lines of coloured tokens; each `color` is a CSS variable, e.g.
 * `var(--shiki-token-keyword)`. SqlView renders them as React text, so nothing in the SQL can
 * become HTML.
 */
export async function sqlTokens(code: string): Promise<ThemedToken[][]> {
  const shiki = await getHighlighter()
  return shiki.codeToTokensBase(code, { lang: 'sql', theme: 'querymind' })
}
