/**
 * The rules a question must pass before it is sent: the same ones the API checks (AskRequest in
 * src/querymind/api/schemas.py), so the user hears about a problem at once, not after a round trip.
 */

/** AskRequest.question's max_length. question.test.ts checks it against web/openapi.json. */
export const MAX_QUESTION_LENGTH = 1000

/**
 * The length the API counts: after trimming (str_strip_whitespace), in characters (code points).
 * JavaScript's `.length` counts UTF-16 units, so an emoji would count twice; `Array.from` doesn't.
 */
export function questionLength(text: string): number {
  return Array.from(text.trim()).length
}

export type QuestionCheck = { ok: true; question: string } | { ok: false; error: string }

/** The trimmed question if it can be sent, or a message that says how to fix it. */
export function checkQuestion(text: string): QuestionCheck {
  const question = text.trim()
  if (!question) return { ok: false, error: 'Type a question first.' }
  const over = questionLength(question) - MAX_QUESTION_LENGTH
  if (over > 0) {
    return {
      ok: false,
      error: `Questions can be at most ${MAX_QUESTION_LENGTH.toLocaleString('en')} characters. Remove ${over.toLocaleString('en')} to send it.`,
    }
  }
  return { ok: true, question }
}

/**
 * What to ask after a clarifying question: the original question with the choice in brackets, so it
 * stands on its own. If that goes over the limit, just the choice: the
 * conversation's history on the server still holds the original question.
 */
export function clarifiedQuestion(question: string, choice: string): string {
  const both = `${question} (${choice.trim()})`
  return questionLength(both) <= MAX_QUESTION_LENGTH ? both : choice.trim()
}

// Screen readers hear the counter only at these points, not on every key press.
const ANNOUNCE_AT = [10, 50, 100]

/**
 * What a screen reader should hear about the limit, or '' while there is plenty of room. The text
 * only changes when a threshold is crossed, so a polite live region speaks it once per threshold.
 */
export function limitAnnouncement(length: number): string {
  const left = MAX_QUESTION_LENGTH - length
  if (left < 0) return `Over the ${MAX_QUESTION_LENGTH.toLocaleString('en')}-character limit.`
  const threshold = ANNOUNCE_AT.find((t) => left <= t)
  return threshold === undefined ? '' : `${threshold} characters or fewer left.`
}
