import { CircleAlert, MessageCircleQuestion, SendHorizontal } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import type { ClarificationResponse } from '@/api/client'
import { Button } from '@/components/ui/button'

import { checkQuestion, clarifiedQuestion } from './question'

// Options are written by the LLM, so they can be long: they wrap instead of overflowing the cell.
const CHOICE_BUTTON =
  'h-auto min-h-9 py-1.5 text-left whitespace-normal aria-disabled:cursor-not-allowed aria-disabled:opacity-50'

type ClarificationCellProps = {
  reply: ClarificationResponse
  /** A question is running: the choices wait (one question at a time). */
  busy: boolean
  /** Asks the question again with the choice in it, in the same conversation. */
  onAnswer: (question: string) => void
}

/** The question was ambiguous: the question back, its options as buttons, or your own answer. */
export function ClarificationCell({ reply, busy, onAnswer }: ClarificationCellProps) {
  const id = useId()
  const [typing, setTyping] = useState(false)
  const toggle = useRef<HTMLButtonElement>(null)
  const questionId = `${id}-question`
  const formId = `${id}-form`

  function close() {
    setTyping(false)
    // The form is going away with the focus in it: put the focus back where it came from.
    toggle.current?.focus()
  }

  return (
    <div className="flex items-start gap-2 text-sm">
      <MessageCircleQuestion aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      <div className="min-w-0 flex-1">
        <p id={questionId} className="font-medium">
          {reply.clarifying_question}
        </p>
        <ul aria-labelledby={questionId} className="mt-2 flex flex-wrap gap-2">
          {reply.options.map((option) => (
            <li key={option}>
              {/* aria-disabled, not disabled: a disabled button would drop keyboard focus. */}
              <Button
                type="button"
                variant="outline"
                aria-disabled={busy}
                className={CHOICE_BUTTON}
                onClick={() => {
                  if (!busy) onAnswer(clarifiedQuestion(reply.question, option))
                }}
              >
                {option}
              </Button>
            </li>
          ))}
          <li>
            <Button
              ref={toggle}
              type="button"
              variant="ghost"
              aria-expanded={typing}
              aria-controls={typing ? formId : undefined}
              className={CHOICE_BUTTON}
              onClick={() => setTyping((open) => !open)}
            >
              Something else…
            </Button>
          </li>
        </ul>
        {typing && (
          <OwnAnswerForm
            id={formId}
            question={reply.question}
            busy={busy}
            onAnswer={(question) => {
              close()
              onAnswer(question)
            }}
            onCancel={close}
          />
        )}
      </div>
    </div>
  )
}

type OwnAnswerFormProps = {
  id: string
  question: string
  busy: boolean
  onAnswer: (question: string) => void
  onCancel: () => void
}

/** "Something else…": the answer in your own words. Enter sends, Escape closes. */
function OwnAnswerForm({ id, question, busy, onAnswer, onCancel }: OwnAnswerFormProps) {
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const inputId = `${id}-input`
  const errorId = `${id}-error`

  // The form opened because of a click on "Something else…": typing is the next step.
  useEffect(() => input.current?.focus(), [])

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (busy) return
    if (!text.trim()) {
      setError('Type your answer first.')
      return
    }
    const check = checkQuestion(clarifiedQuestion(question, text))
    if (check.ok) onAnswer(check.question)
    else setError(check.error)
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Escape') onCancel()
  }

  return (
    <form id={id} onSubmit={onSubmit} className="mt-3 max-w-xl">
      <label htmlFor={inputId} className="font-medium">
        Your answer
      </label>
      <div className="mt-1.5 flex gap-2">
        <input
          ref={input}
          id={inputId}
          type="text"
          value={text}
          onChange={(event) => {
            setText(event.target.value)
            setError(null)
          }}
          onKeyDown={onKeyDown}
          aria-invalid={error !== null}
          aria-describedby={error ? errorId : undefined}
          className="h-9 min-w-0 flex-1 rounded-lg border border-input bg-muted px-3 text-base outline-none focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger pointer-coarse:min-h-11"
        />
        <Button type="submit" size="lg" aria-disabled={busy} className={CHOICE_BUTTON}>
          <SendHorizontal aria-hidden="true" />
          Send
        </Button>
      </div>
      {error && (
        <p id={errorId} role="alert" className="mt-2 flex items-start gap-1.5 text-danger">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}
    </form>
  )
}
