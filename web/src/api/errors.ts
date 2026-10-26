// What to tell the user for each error code; the API's own message is shown below it.
// Ported from ui/app.py ERROR_TITLES (keep the two in sync until Streamlit is retired in U6.6),
// plus the client-side codes from client.ts (bad_response) and the server's catch-all (internal_error).
export const ERROR_TITLES = {
  unsafe_sql: "I couldn't answer that safely.",
  invalid_request: "That question can't be sent.",
  query_timeout: 'The query took too long.',
  query_failed: "The database couldn't run the query.",
  db_unavailable: "The database isn't reachable right now.",
  llm_unavailable: 'The AI model is unavailable right now. Please try again.',
  llm_bad_output: 'The AI model gave an unusable answer. Please try rephrasing.',
  api_unreachable: "The QueryMind API isn't reachable.",
  api_timeout: 'The API took too long to answer.',
  bad_response: 'The API sent an unexpected response.',
  internal_error: 'Something went wrong on the server.',
} as const satisfies Record<string, string>

export type ErrorCode = keyof typeof ERROR_TITLES

/** The title for an error code; codes we don't know yet get a generic one. */
export function errorTitle(code: string): string {
  return code in ERROR_TITLES ? ERROR_TITLES[code as ErrorCode] : 'Something went wrong.'
}
