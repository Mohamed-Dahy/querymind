import { Loader2, Trash2 } from 'lucide-react'
import { useId, useRef, useState, type ReactNode } from 'react'

import { asApiError, type ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

import { FormError } from './FormError'

type ConfirmButtonProps = {
  /** The button's words, and the dialog's confirm button's. */
  label: string
  /** Which one, read only by screen readers after `label` ("Delete Sales"): for lists of them. */
  which?: string
  busyLabel: string
  title: string
  description: ReactNode
  /** The change itself (a DELETE). Throws to keep the dialog open with the error. */
  onConfirm: () => Promise<void>
  /**
   * After it succeeded and the dialog has closed: update the page (the button usually goes with
   * what it removed) and move focus somewhere meaningful.
   */
  onDone: () => void
}

/**
 * A button for something that can't be undone (removing a key, a database, a member), behind a
 * confirm dialog. Cancel or Escape: focus back on the button. Done: the page changes only once the
 * dialog has closed, so focus has a place to land (`onDone`), never <body>.
 */
export function ConfirmButton({
  label,
  which,
  busyLabel,
  title,
  description,
  onConfirm,
  onDone,
}: ConfirmButtonProps) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const done = useRef(false)
  const errorId = useId()

  async function confirm() {
    setBusy(true)
    setError(null)
    try {
      await onConfirm()
      done.current = true
      setOpen(false)
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (busy) return // the request is on its way: its answer decides
        setOpen(next)
        if (next) setError(null)
      }}
    >
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className="pointer-coarse:min-h-11">
          <Trash2 aria-hidden="true" />
          {label}
          {which !== undefined && (
            <>
              {' '}
              <span className="sr-only">{which}</span>
            </>
          )}
        </Button>
      </DialogTrigger>
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (!done.current) return // Cancel: back to the button, as usual
          event.preventDefault()
          onDone()
        }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && <FormError id={errorId} error={error} />}
        <DialogFooter>
          <DialogClose asChild>
            <Button type="button" variant="outline" disabled={busy}>
              Cancel
            </Button>
          </DialogClose>
          <Button
            type="button"
            variant="destructive"
            disabled={busy}
            aria-busy={busy}
            aria-describedby={error ? errorId : undefined}
            onClick={() => void confirm()}
          >
            {busy && (
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            )}
            {busy ? busyLabel : label}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
