/**
 * The server's default limits (`src/querymind/config.py`), shown on the landing page, its circuit and
 * the home console. One place, so they can't drift apart. An operator can change them, so the pages
 * say "by default". If config.py's defaults change, change them here.
 */
export const DEFAULT_LIMITS = {
  /** `query_timeout_seconds`: the statement timeout on every query. */
  timeoutSeconds: 10,
  /** `max_rows`: the row limit on every query. */
  maxRows: 1000,
  /** `max_sql_attempts`: tries per question, the first one and the repairs. */
  sqlAttempts: 3,
} as const

/** "10 s" */
export const TIMEOUT_TEXT = `${DEFAULT_LIMITS.timeoutSeconds} s`
/** "1,000 rows" */
export const ROWS_TEXT = `${DEFAULT_LIMITS.maxRows.toLocaleString('en')} rows`
/** "3 tries" */
export const TRIES_TEXT = `${DEFAULT_LIMITS.sqlAttempts} tries`
