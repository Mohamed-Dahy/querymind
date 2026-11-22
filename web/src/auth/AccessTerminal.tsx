import { useEffect, useState } from 'react'

import type { AskResponse } from '@/api/client'
import { ChannelBar, CropMarks } from '@/components/Instrument'
import { cn } from '@/lib/utils'
import { useMotionSafe } from '@/motion'
// The same recorded run as the landing page's demo (scripts/record_fixtures.py): a real question,
// the SQL the model really wrote, the database's real error, the repair, and the real answer.
import recordedReply from '@/mocks/recorded/repair.json'

const REPLY = recordedReply as AskResponse

type Tone = 'prompt' | 'step' | 'fail' | 'repair' | 'ok' | 'answer'
type Line = { tone: Tone; tag: string; text: string }

/** The run, line by line, every word from the recording. */
function transcript(reply: AskResponse): Line[] {
  const failed = reply.attempts[0]
  const timing =
    typeof reply.duration_ms === 'number' ? ` · ${(reply.duration_ms / 1000).toFixed(1)} s` : ''
  const lines: Line[] = [
    { tone: 'prompt', tag: '>', text: reply.question },
    { tone: 'step', tag: 'retrieve', text: `tables: ${reply.tables_used.join(', ')}` },
  ]
  if (failed) {
    lines.push(
      { tone: 'step', tag: 'write sql', text: failed.sql },
      { tone: 'step', tag: 'validate', text: 'one read-only SELECT · ok' },
      { tone: 'fail', tag: 'run', text: failed.message },
      { tone: 'repair', tag: 'repair', text: reply.sql },
    )
  } else {
    lines.push({ tone: 'step', tag: 'write sql', text: reply.sql })
  }
  lines.push(
    { tone: 'ok', tag: 'run', text: `${reply.row_count} rows · read-only role` },
    { tone: 'answer', tag: 'answer', text: reply.answer },
    {
      tone: 'step',
      tag: 'readout',
      text: `${reply.confidence} confidence · ${reply.sql_attempts} tries${timing}`,
    },
  )
  return lines
}

const LINES = transcript(REPLY)
/** Time between two lines, and between two typed characters of the question. */
const LINE_MS = 520
const CHAR_MS = 28

const MARK: Record<Tone, { mark: string; colour: string }> = {
  prompt: { mark: '', colour: 'text-signal' },
  step: { mark: '·', colour: 'text-fg-muted' },
  fail: { mark: '✗', colour: 'text-danger' },
  repair: { mark: '↻', colour: 'text-warn' },
  ok: { mark: '✓', colour: 'text-signal' },
  answer: { mark: '=', colour: 'text-fg' },
}

/**
 * Beside the login and sign-up forms (U9.2): a terminal that prints a real recorded session, the
 * question typed in, then each step, the failed try, the repair and the answer. It runs once.
 * Decoration for sighted visitors, so hidden from screen readers (the form is what matters there);
 * under reduced motion (and in unit tests) the whole session is simply shown.
 */
export function AccessTerminal() {
  const { animate } = useMotionSafe()
  // How far the print-out has got: whole lines, and characters of the question.
  const [lines, setLines] = useState(0)
  const [chars, setChars] = useState(0)
  const question = LINES[0]?.text ?? ''
  const shownLines = animate ? lines : LINES.length
  const typed = animate ? chars : question.length
  const done = shownLines >= LINES.length

  useEffect(() => {
    if (!animate || done) return undefined
    // First type the question, a character at a time; then print a line at a time.
    const typing = lines === 0 && chars < question.length
    const timer = setTimeout(
      () => (typing ? setChars((count) => count + 1) : setLines((count) => count + 1)),
      typing ? CHAR_MS : LINE_MS,
    )
    return () => clearTimeout(timer)
  }, [animate, done, lines, chars, question.length])

  return (
    <div aria-hidden="true" className="relative">
      <CropMarks />
      <div className="overflow-hidden rounded-[6px] border bg-surface">
        <ChannelBar
          live={!done}
          label={
            <>
              <span className="text-fg">TTY-1</span>
              <span className="truncate">recorded session · northwind · read-only</span>
            </>
          }
        />
        {/* Tall enough for the whole session, so the page (and the form beside it) never moves
            while it prints. */}
        <ol className="min-h-[23.5rem] space-y-2 p-4 font-mono text-[0.8125rem] leading-relaxed">
          {LINES.map((line, index) => {
            // The question line is shown as it is typed; the others appear whole, in turn.
            if (index === 0) {
              return (
                <li key={index} className="flex gap-2">
                  <span className="text-signal">&gt;</span>
                  <span className="text-fg">
                    {question.slice(0, typed)}
                    {shownLines === 0 && <Caret />}
                  </span>
                </li>
              )
            }
            if (index > shownLines) return null
            const { mark, colour } = MARK[line.tone]
            return (
              <li
                key={index}
                className="grid grid-cols-[1ch_6.5rem_minmax(0,1fr)] gap-x-2 animate-in duration-200 fade-in-0 motion-reduce:animate-none"
              >
                <span className={colour}>{mark}</span>
                <span className="text-fg-muted">{line.tag}</span>
                <span
                  className={cn(
                    'wrap-break-word',
                    line.tone === 'answer' ? 'font-sans text-base font-medium text-fg' : colour,
                    line.tone === 'step' && 'text-fg',
                  )}
                >
                  {line.text}
                  {index === shownLines && !done && <Caret />}
                </span>
              </li>
            )
          })}
        </ol>
      </div>
    </div>
  )
}

/** Where the next character goes: a solid block, not blinking (nothing here flashes). */
function Caret() {
  return <span className="ml-0.5 inline-block h-[1em] w-[0.55em] translate-y-[0.15em] bg-signal" />
}
