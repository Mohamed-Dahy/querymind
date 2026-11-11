// What to tell the user for each error code; the API's own message is shown below it.
// First written for the Streamlit UI (retired in U6.6), one title per API error code (errors.py),
// plus the client-side codes from client.ts (bad_response) and the server's catch-all (internal_error).
export const ERROR_TITLES = {
  unsafe_sql: "I couldn't answer that safely.",
  invalid_request: "That question can't be sent.",
  query_timeout: 'The query took too long.',
  query_failed: "The database couldn't run the query.",
  db_unavailable: "The database isn't reachable right now.",
  conversation_not_found: 'Conversation not found.',
  turn_not_found: "This question can't be run again.",
  history_unavailable: "History isn't available.",
  evals_unavailable: "The eval results aren't available.",
  not_authenticated: "You're logged out.",
  invalid_login: "Couldn't log you in.",
  email_taken: 'That email already has an account.',
  forbidden: "Your role can't open this.",
  accounts_unavailable: "Logins aren't working right now.",
  llm_rate_limited: 'The AI model has reached its usage limit.',
  rate_limited: "You've reached QueryMind's limit for now.",
  llm_unavailable: 'The AI model is unavailable right now. Please try again.',
  llm_bad_output: 'The AI model gave an unusable answer. Please try rephrasing.',
  llm_key_rejected: 'The AI provider refused the API key.',
  llm_key_invalid: "That key can't be used.",
  llm_key_unreadable: "The saved key can't be read.",
  connection_unsafe: "QueryMind can't connect to that address.",
  connection_unreachable: "Couldn't reach the database.",
  connection_login_failed: 'The database refused the login.',
  connection_tls_failed: "The encrypted connection didn't work.",
  connection_writable: 'That login can change data.',
  connection_not_found: 'Database not found.',
  connection_name_taken: 'That name is already used.',
  connection_no_tables: 'That login can read no tables.',
  connection_unreadable: "The saved database can't be read.",
  conversation_other_database: 'This conversation is about another database.',
  llm_key_required: 'Your own database needs your own LLM key.',
  onboarding_running: 'This database is being onboarded.',
  dictionary_not_found: 'No data dictionary yet.',
  dictionary_invalid: "That change doesn't fit the dictionary.",
  api_unreachable: "The QueryMind API isn't reachable.",
  api_timeout: 'The API took too long to answer.',
  stream_interrupted: 'The connection to the API was lost.',
  stopped: 'You stopped this question.',
  bad_response: 'The API sent an unexpected response.',
  internal_error: 'Something went wrong on the server.',
} as const satisfies Record<string, string>

export type ErrorCode = keyof typeof ERROR_TITLES

/** The title for an error code; codes we don't know yet get a generic one. */
export function errorTitle(code: string): string {
  return code in ERROR_TITLES ? ERROR_TITLES[code as ErrorCode] : 'Something went wrong.'
}
