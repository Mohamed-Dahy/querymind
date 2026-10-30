import { CircleAlert, LoaderCircle, SendHorizontal } from 'lucide-react'
import { useId, useState, type FormEvent, type KeyboardEvent } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import { checkQuestion, limitAnnouncement, MAX_QUESTION_LENGTH, questionLength } from './question'

// Only the hint text differs: both Ctrl and ⌘ send on every system.
const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform)
const MODIFIER = IS_MAC ? '⌘' : 'Ctrl'

type ComposerProps = {
  /** Called with the trimmed question, only when it passes the checks in question.ts. */
  onSubmit: (question: string) => void
  /** A question is being answered: sending waits, typing the next one doesn't. */
  busy?: boolean
  placeholder?: string
}

/** The question box: grows as you type, Ctrl/⌘ + Enter sends, and it says why it won't send. */
export function Composer({
  onSubmit,
  busy = false,
  placeholder = 'e.g. Which category sold the most in 1997?',
}: ComposerProps) {
  const id = useId()
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)

  const length = questionLength(text)
  const tooLong = length > MAX_QUESTION_LENGTH
  const inputId = `${id}-question`
  const hintId = `${id}-hint`
  const errorId = `${id}-error`

  function send() {
    if (busy) return
    const check = checkQuestion(text)
    if (!check.ok) {
      setError(check.error)
      return
    }
    setError(null)
    setText('')
    onSubmit(check.question)
  }

  function onFormSubmit(event: FormEvent) {
    event.preventDefault()
    send()
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Plain Enter is a new line. isComposing: an Enter that confirms an IME word (Chinese,
    // Japanese…) must not send the half-typed question.
    if (
      event.key === 'Enter' &&
      (event.ctrlKey || event.metaKey) &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault()
      send()
    }
  }

  return (
    <form onSubmit={onFormSubmit} className="rounded-cell border bg-surface p-3 shadow-sm">
      <label htmlFor={inputId} className="text-sm font-medium">
        Your question
      </label>
      <textarea
        id={inputId}
        value={text}
        onChange={(event) => {
          setText(event.target.value)
          setError(null)
        }}
        onKeyDown={onKeyDown}
        placeholder={placeholder}
        rows={2}
        aria-invalid={error !== null || tooLong}
        aria-describedby={error ? `${hintId} ${errorId}` : hintId}
        // field-sizing-content: the box grows with the text, up to max-h-60, then scrolls.
        className={cn(
          'mt-1.5 field-sizing-content max-h-60 min-h-16 w-full resize-none overflow-y-auto rounded-lg border',
          'border-input bg-muted px-3 py-2 text-base placeholder:text-fg-muted',
          'outline-none focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger',
        )}
      />

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2">
        <p id={hintId} className="text-xs text-fg-muted">
          <Kbd>{MODIFIER}</Kbd> + <Kbd>Enter</Kbd> to send · <Kbd>Enter</Kbd> for a new line
        </p>
        <span
          // The live region below speaks for it; reading "17 / 1,000" on every key would be noise.
          aria-hidden="true"
          className={cn(
            'ml-auto text-xs tabular-nums',
            tooLong ? 'font-medium text-danger' : 'text-fg-muted',
          )}
        >
          {length.toLocaleString('en')} / {MAX_QUESTION_LENGTH.toLocaleString('en')}
        </span>
        {/* aria-disabled, not disabled: a disabled button would drop keyboard focus. */}
        <Button type="submit" size="lg" aria-disabled={busy} className="min-w-24">
          {busy ? (
            <>
              <LoaderCircle
                aria-hidden="true"
                className="animate-spin motion-reduce:animate-none"
              />
              Asking…
            </>
          ) : (
            <>
              <SendHorizontal aria-hidden="true" />
              Ask
            </>
          )}
        </Button>
      </div>

      {error && (
        <p id={errorId} role="alert" className="mt-2 flex items-start gap-1.5 text-sm text-danger">
          <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {error}
        </p>
      )}
      <p aria-live="polite" className="sr-only">
        {limitAnnouncement(length)}
      </p>
    </form>
  )
}

function Kbd({ children }: { children: string }) {
  return (
    <kbd className="rounded border border-input bg-muted px-1 font-mono text-xs">{children}</kbd>
  )
}
