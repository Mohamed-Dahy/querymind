import { useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Loader2, Trash2, TriangleAlert } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type RefObject } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { errorTitle } from '@/api/errors'
import { useSession } from '@/api/session'
import {
  deleteLLMKey,
  LLM_KEY_QUERY,
  saveLLMKey,
  useLLMKey,
  useProviders,
  type LLMKeyInfo,
  type ProviderOption,
} from '@/api/settings'
import { FIELD } from '@/components/field'
import { SubmitButton } from '@/components/SubmitButton'
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
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'

// The API's limits (api/llm_keys.py), checked here first so the user hears it before sending.
const MIN_KEY = 8
const MAX_KEY = 512
const MAX_MODEL = 200

const SAVED_DATE = new Intl.DateTimeFormat('en', { dateStyle: 'medium' })

function asApiError(caught: unknown): ApiError {
  return caught instanceof ApiError
    ? caught
    : new ApiError('internal_error', 'Something went wrong. Please try again.')
}

/**
 * Settings → LLM key (U7.1b): which provider and model answer this workspace's questions. Every
 * member sees which one; admins save, replace or remove it. The key itself never comes back from
 * the API, only its last 4 characters.
 */
export default function KeySettings() {
  const key = useLLMKey()
  const providers = useProviders()
  const admin = useSession()?.user.role === 'admin'
  const headingId = useId()
  // Where focus goes after "Remove": the button that opened the dialog is gone by then.
  const heading = useRef<HTMLHeadingElement>(null)

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} ref={heading} tabIndex={-1} className="rounded-lg text-xl outline-none">
        LLM key
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Which AI model answers this workspace&rsquo;s questions, and on whose account.
      </p>

      <div className="mt-4 space-y-6">
        {key.isPending || providers.isPending ? (
          <Skeleton aria-hidden="true" className="h-40 w-full motion-reduce:animate-none" />
        ) : key.isError || providers.isError ? (
          <LoadError
            error={key.error ?? providers.error}
            onRetry={() => {
              if (key.isError) void key.refetch()
              if (providers.isError) void providers.refetch()
            }}
            message="The key settings can't be shown right now."
          />
        ) : (
          <>
            <CurrentKey info={key.data} admin={admin} focusAfterRemove={heading} />
            {admin ? (
              <KeyForm providers={providers.data} saved={key.data} />
            ) : (
              <p className="text-sm text-fg-muted">
                Only an admin of this workspace can save or remove the key.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}

type CurrentKeyProps = {
  info: LLMKeyInfo | null
  admin: boolean
  focusAfterRemove: RefObject<HTMLElement | null>
}

/** What answers questions now: the workspace's own key, or the shared demo key. */
function CurrentKey({ info, admin, focusAfterRemove }: CurrentKeyProps) {
  if (info === null) {
    return (
      <div className="rounded-cell border bg-surface p-4">
        <p className="font-medium">QueryMind&rsquo;s free demo key</p>
        <p className="mt-1 text-sm text-fg-muted">
          Questions about the demo databases run on a shared key, with limits for everyone. To ask
          about your own databases, {admin ? 'save' : 'an admin can save'} your own key.
        </p>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-start justify-between gap-3 rounded-cell border bg-surface p-4">
      <div className="min-w-0">
        <p className="font-medium">Your own {info.provider_label} key</p>
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
          <dt className="text-fg-muted">Model</dt>
          <dd className="font-mono break-all">{info.model}</dd>
          <dt className="text-fg-muted">Key</dt>
          <dd>{info.key_hint ? `ends in …${info.key_hint}` : 'saved'}</dd>
          <dt className="text-fg-muted">Saved</dt>
          <dd>
            <time dateTime={info.updated_at}>{SAVED_DATE.format(new Date(info.updated_at))}</time>
          </dd>
        </dl>
      </div>
      {admin && <RemoveKey info={info} focusAfterRemove={focusAfterRemove} />}
    </div>
  )
}

/**
 * "Remove key", behind a confirm: it can't be undone (the key exists nowhere else, so getting it
 * back means pasting it again). The page changes only once the dialog has closed, so focus has a
 * place to land: the tab's heading.
 */
function RemoveKey({
  info,
  focusAfterRemove,
}: {
  info: LLMKeyInfo
  focusAfterRemove: RefObject<HTMLElement | null>
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const removed = useRef(false)
  const errorId = useId()

  async function remove() {
    setBusy(true)
    setError(null)
    try {
      await deleteLLMKey()
      removed.current = true
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
        if (busy) return // the delete is on its way: its answer decides
        setOpen(next)
        if (next) setError(null)
      }}
    >
      {/* The dialog's trigger: Cancel or Escape gives focus back to it. */}
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className="pointer-coarse:min-h-11">
          <Trash2 aria-hidden="true" />
          Remove key
        </Button>
      </DialogTrigger>
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (!removed.current) return // Cancel: back to "Remove key", as usual
          event.preventDefault()
          queryClient.setQueryData(LLM_KEY_QUERY, null)
          focusAfterRemove.current?.focus()
          toast.success('Key removed. Questions use the demo key again.')
        }}
      >
        <DialogHeader>
          <DialogTitle>Remove your {info.provider_label} key?</DialogTitle>
          <DialogDescription>
            Questions go back to QueryMind&rsquo;s free demo key, and your own databases can&rsquo;t
            be asked until a key is saved again. To use this key again, you&rsquo;ll have to paste
            it again.
          </DialogDescription>
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
            onClick={() => void remove()}
          >
            {busy && (
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            )}
            {busy ? 'Removing…' : 'Remove key'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function exampleOf(providers: ProviderOption[], id: ProviderOption['id'] | undefined): string {
  return providers.find((option) => option.id === id)?.example_model ?? ''
}

/**
 * Save or replace the key. The server makes one small test call with it first (a few seconds),
 * so the result is known before anything is saved. The key is never kept in React state or the
 * query cache: it is read from the field on submit, and the field is emptied once it is saved.
 */
function KeyForm({ providers, saved }: { providers: ProviderOption[]; saved: LLMKeyInfo | null }) {
  const queryClient = useQueryClient()
  const [providerId, setProviderId] = useState(saved?.provider ?? providers[0]?.id)
  const [model, setModel] = useState(saved?.model ?? exampleOf(providers, providerId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number>()
  const keyField = useRef<HTMLInputElement>(null)
  const ids = {
    heading: useId(),
    provider: useId(),
    model: useId(),
    modelHint: useId(),
    key: useId(),
    keyHint: useId(),
    error: useId(),
  }
  const provider = providers.find((option) => option.id === providerId) ?? providers[0]
  if (provider === undefined) return null // the API always lists some; nothing to choose from

  function chooseProvider(next: ProviderOption['id']) {
    // A model the user typed stays; the previous provider's example becomes the new one's.
    if (model.trim() === '' || model === provider?.example_model) {
      setModel(exampleOf(providers, next))
    }
    setProviderId(next)
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy || provider === undefined) return
    setBusy(true)
    setError(null)
    try {
      const info = await saveLLMKey({
        provider: provider.id,
        model: model.trim(),
        // Copied keys often bring a space or a line break along.
        api_key: keyField.current?.value.trim() ?? '',
      })
      if (keyField.current) keyField.current.value = ''
      queryClient.setQueryData(LLM_KEY_QUERY, info)
      toast.success(`Key saved. The next question runs on ${info.provider_label}, ${info.model}.`)
    } catch (caught) {
      const failure = asApiError(caught)
      setError(failure)
      if (failure.retryAfterSeconds !== undefined) {
        setRetryAt(Date.now() + failure.retryAfterSeconds * 1000)
      }
    } finally {
      setBusy(false)
    }
  }

  const described = (hint: string) => [hint, error && ids.error].filter(Boolean).join(' ')

  return (
    <form
      aria-labelledby={ids.heading}
      onSubmit={(event) => void onSubmit(event)}
      className="space-y-4 rounded-cell border bg-surface p-4"
    >
      <div>
        <h3 id={ids.heading} className="font-medium">
          {saved ? 'Replace the key' : 'Save your own key'}
        </h3>
        <p className="mt-1 text-sm text-fg-muted">
          QueryMind makes one small test call with it before saving. It is stored encrypted and
          never shown again: only its last 4 characters.
        </p>
      </div>

      <div>
        <label htmlFor={ids.provider} className="text-sm font-medium">
          Provider
        </label>
        <select
          id={ids.provider}
          value={provider.id}
          onChange={(event) => chooseProvider(event.target.value as ProviderOption['id'])}
          className={FIELD}
        >
          {providers.map((option) => (
            <option key={option.id} value={option.id}>
              {option.label}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label htmlFor={ids.model} className="text-sm font-medium">
          Model
        </label>
        <input
          id={ids.model}
          value={model}
          onChange={(event) => setModel(event.target.value)}
          required
          maxLength={MAX_MODEL}
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-describedby={described(ids.modelHint)}
          className={`${FIELD} font-mono`}
        />
        <p id={ids.modelHint} className="mt-1.5 text-xs text-fg-muted">
          Any model {provider.label} serves, for example {provider.example_model}. The test call
          checks it.
        </p>
      </div>

      <div>
        <label htmlFor={ids.key} className="text-sm font-medium">
          API key
        </label>
        <input
          ref={keyField}
          id={ids.key}
          type="password"
          required
          minLength={MIN_KEY}
          maxLength={MAX_KEY}
          // Not the site's login password: the browser must not offer to save or fill it.
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          aria-invalid={error?.code === 'llm_key_invalid' || undefined}
          aria-describedby={described(ids.keyHint)}
          className={`${FIELD} font-mono`}
        />
        <p id={ids.keyHint} className="mt-1.5 text-xs text-fg-muted">
          No key yet?{' '}
          <a
            href={provider.keys_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-info underline underline-offset-4"
          >
            Make one at {provider.label}
            <ExternalLink aria-hidden="true" className="size-3" />
            <span className="sr-only">(opens in a new tab)</span>
          </a>
        </p>
      </div>

      {error && <FormError id={ids.error} error={error} />}

      <SubmitButton
        label="Test and save"
        busyLabel="Testing the key…"
        busy={busy}
        retryAt={retryAt}
      />
    </form>
  )
}

/** Why it failed, in our words (the title) and the API's (its fix). */
function FormError({ id, error }: { id: string; error: ApiError }) {
  return (
    <div
      id={id}
      role="alert"
      className="flex items-start gap-1.5 rounded-lg border border-danger px-3 py-2 text-sm"
    >
      <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
      <div>
        <p className="font-medium">{errorTitle(error.code)}</p>
        <p className="text-fg-muted">{error.message}</p>
      </div>
    </div>
  )
}
