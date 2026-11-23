import { CircleAlert, LoaderCircle, SendHorizontal } from 'lucide-react'
import {
  useId,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type Ref,
} from 'react'

import { Kbd } from '@/components/Kbd'
import { Button } from '@/components/ui/button'
import { MODIFIER } from '@/lib/keys'
import { cn } from '@/lib/utils'

import { checkQuestion, limitAnnouncement, MAX_QUESTION_LENGTH, questionLength } from './question'

/** What the page can do to the composer from outside (React's `ref` on a component). */
export type ComposerHandle = {
  focus: () => void
  /** Replaces the text and puts the focus there, ready to edit. */
  fill: (text: string) => void
}

type ComposerProps = {
  /** Called with the trimmed question, only when it passes the checks in question.ts. */
  onSubmit: (question: string) => void
  /** A question is being answered: sending waits, typing the next one doesn't. */
  busy?: boolean
  placeholder?: string
  ref?: Ref<ComposerHandle>
  /** The id of text that says more about this question (e.g. what it follows up on). */
  describedBy?: string | undefined
  /**
   * `dock` (the default): the canvas's command bar (U9.4), compact: the box and Ask on one row, the
   * hint under them. `console`: the home console's centrepiece (U9.3), roomy, inside a panel that
   * draws the frame and lights up on focus. Only classes differ, never the elements, so switching
   * from one to the other keeps the box (and its focus and text).
   */
  variant?: 'console' | 'dock'
}

/** The question box: grows as you type, Ctrl/⌘ + Enter sends, and it says why it won't send. */
export function Composer({
  onSubmit,
  busy = false,
  placeholder = 'e.g. Which category sold the most in 1997?',
  ref,
  describedBy,
  variant = 'dock',
}: ComposerProps) {
  const id = useId()
  const [text, setText] = useState('')
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLTextAreaElement>(null)
  useImperativeHandle(
    ref,
    () => ({
      focus: () => box.current?.focus(),
      fill: (next) => {
        setText(next)
        setError(null)
        box.current?.focus()
      },
    }),
    [],
  )

  const length = questionLength(text)
  const tooLong = length > MAX_QUESTION_LENGTH
  const inputId = `${id}-question`
  const hintId = `${id}-hint`
  const errorId = `${id}-error`
  // The dock is a grid: label on row 1, the box and Ask on row 2, the hint and count on row 3.
  // The row with the hint and Ask becomes `contents` there, so its children join that grid.
  const dock = variant === 'dock'

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
    // Signal Lab: a command line (U8.3). Where it sits decides its frame: the home console's panel
    // (U9.3) or the canvas's docked bar (U9.4).
    <form
      onSubmit={onFormSubmit}
      className={cn(dock ? 'grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 py-3' : 'p-3')}
    >
      <label
        htmlFor={inputId}
        className={cn(
          'flex items-center gap-2 font-medium',
          dock ? 'col-span-2 row-start-1 text-xs' : 'text-sm',
        )}
      >
        <span aria-hidden="true" className="font-mono text-signal">
          {'>_'}
        </span>
        Your question
      </label>
      <textarea
        ref={box}
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
        aria-describedby={[describedBy, hintId, error && errorId].filter(Boolean).join(' ')}
        // field-sizing-content: the box grows with the text, up to max-h-60, then scrolls.
        className={cn(
          'mt-1.5 field-sizing-content max-h-60 w-full resize-none overflow-y-auto rounded-lg border',
          dock ? 'col-start-1 row-start-2 min-h-11' : 'min-h-16',
          // An empty dock box on a phone: the example in the placeholder stays on one line (cut
          // with "…"), or the box would grow to three lines just to show it. Typing wraps again.
          dock && text === '' && 'overflow-x-hidden text-ellipsis whitespace-nowrap',
          'border-input bg-bg px-3 py-2 text-base placeholder:text-fg-muted',
          'outline-none focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger',
        )}
      />

      <div className={dock ? 'contents' : 'mt-2 flex flex-wrap items-center gap-x-4 gap-y-2'}>
        {/* On a phone the dock keeps the hint for screen readers only (it is the box's
            description), so the bar stays short; a touch keyboard has no Ctrl + Enter anyway. */}
        <p
          id={hintId}
          className={cn(
            'font-mono text-xs text-fg-muted',
            dock && 'col-start-1 row-start-3 mt-1.5 max-sm:sr-only',
          )}
        >
          <Kbd>{MODIFIER}</Kbd> + <Kbd>Enter</Kbd> to send · <Kbd>Enter</Kbd> for a new line
        </p>
        <span
          // The live region below speaks for it; reading "17 / 1,000" on every key would be noise.
          aria-hidden="true"
          className={cn(
            'text-xs tabular-nums',
            dock ? 'col-start-2 row-start-3 mt-1.5 text-right max-sm:hidden' : 'ml-auto',
            tooLong ? 'font-medium text-danger' : 'text-fg-muted',
          )}
        >
          {length.toLocaleString('en')} / {MAX_QUESTION_LENGTH.toLocaleString('en')}
        </span>
        {/* aria-disabled, not disabled: a disabled button would drop keyboard focus. */}
        <Button
          type="submit"
          size="lg"
          aria-disabled={busy}
          className={cn(
            'min-w-24',
            dock && 'col-start-2 row-start-2 mt-1.5 h-11 self-end max-sm:min-w-0 max-sm:px-3',
          )}
        >
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
        <p
          id={errorId}
          role="alert"
          className={cn(
            'mt-2 flex items-start gap-1.5 text-sm text-danger',
            dock && 'col-span-2 row-start-4',
          )}
        >
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
