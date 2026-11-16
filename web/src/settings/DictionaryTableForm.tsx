import { useQueryClient } from '@tanstack/react-query'
import { Check, Loader2, ShieldAlert } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type Ref } from 'react'
import { toast } from 'sonner'

import { asApiError, type ApiError } from '@/api/client'
import {
  dictionaryKey,
  refreshDescriptions,
  saveDictionaryTable,
  type DictionaryTable,
} from '@/api/onboarding'
import { FIELD } from '@/components/field'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import {
  isChanged,
  startDraft,
  tableEdit,
  ticked,
  withPersonal,
  type TableDraft,
} from './dictionaryDraft'
import { FormError } from './FormError'

// The API's limits (api/onboarding.py TableEdit): typing stops there instead of a refused save.
const DESCRIPTION_MAX = 500
const COLUMN_MAX = 300

/** A column as the review shows it: its name, and its type when the database could be read. */
export type ColumnShown = { name: string; type?: string }

type TableFormProps = {
  id: string
  table: string
  saved: DictionaryTable
  draft: TableDraft | undefined
  columns: ColumnShown[]
  /** For a table not flagged yet (`pii: null`): what the server masks now, from name guesses. */
  guessed: string[] | undefined
  /** Those guesses couldn't be loaded: the boxes stay locked, never shown as "none". */
  flagsFailed: boolean
  onDraft: (draft: TableDraft | undefined) => void
  /** The save was refused because a run started meanwhile: show that run. */
  onRunning: () => void
}

/**
 * One table's review, for admins: its description, each column's, which columns are personal data
 * (hidden as *** for every role but admin), then Save, or Save and mark reviewed. Edits live in the
 * page's draft (kept while looking at other tables) until saved; see dictionaryDraft.ts for what a
 * save sends, and why.
 */
export function TableForm({
  id,
  table,
  saved,
  draft,
  columns,
  guessed,
  flagsFailed,
  onDraft,
  onRunning,
}: TableFormProps) {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState<'save' | 'review' | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const ids = { heading: useId(), description: useId(), pii: useId(), error: useId() }

  const current = draft ?? startDraft(saved)
  const shown = ticked(saved, draft, guessed)

  function change(next: TableDraft) {
    onDraft(isChanged(next, saved, guessed) ? next : undefined)
  }

  async function save(reviewed: boolean) {
    if (busy) return
    setBusy(reviewed && !saved.reviewed ? 'review' : 'save')
    setError(null)
    try {
      const result = await saveDictionaryTable(id, table, tableEdit(saved, draft, reviewed))
      queryClient.setQueryData(dictionaryKey(id), result)
      onDraft(undefined)
      // The explorer's descriptions and personal-data badges come from the dictionary too.
      refreshDescriptions(queryClient, id)
      toast.success(
        reviewed && !saved.reviewed ? `${table}: saved and marked reviewed.` : `${table}: saved.`,
      )
      // "Save and mark reviewed" goes away once it is: focus needs somewhere to land.
      if (reviewed && !saved.reviewed) heading.current?.focus()
    } catch (caught) {
      const failure = asApiError(caught)
      setError(failure)
      if (failure.code === 'onboarding_running') onRunning()
    } finally {
      setBusy(null)
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    void save(saved.reviewed)
  }

  const describedBy = error ? ids.error : undefined
  return (
    <form
      aria-labelledby={ids.heading}
      onSubmit={onSubmit}
      className="space-y-5 rounded-cell border bg-surface p-4"
    >
      <TableHeading id={ids.heading} ref={heading} table={table} reviewed={saved.reviewed} />

      <div>
        <label htmlFor={ids.description} className="text-sm font-medium">
          What one row is
        </label>
        <textarea
          id={ids.description}
          rows={3}
          maxLength={DESCRIPTION_MAX}
          value={current.description}
          onChange={(event) => change({ ...current, description: event.target.value })}
          aria-describedby={describedBy}
          className={cn(FIELD, 'h-auto py-2')}
        />
      </div>

      <fieldset>
        <legend className="text-sm font-medium">Columns</legend>
        <p id={ids.pii} className="mt-1 text-xs text-fg-muted">
          Personal data is hidden (***) in answers for every role but admin.
          {flagsFailed && shown === undefined
            ? ' Which columns are personal can’t be shown right now: the boxes are locked.'
            : shown === undefined
              ? ' Loading which columns are personal…'
              : ''}
        </p>
        <ul className="mt-3 space-y-4">
          {columns.map(({ name, type }) => (
            <ColumnRow
              key={name}
              name={name}
              type={type}
              text={current.columns[name] ?? ''}
              personal={shown?.has(name)}
              piiHint={ids.pii}
              onText={(text) =>
                change({ ...current, columns: { ...current.columns, [name]: text } })
              }
              onPersonal={(personal) => {
                if (shown) change(withPersonal(current, shown, name, personal))
              }}
            />
          ))}
        </ul>
      </fieldset>

      {error && <FormError id={ids.error} error={error} />}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="submit"
          variant={saved.reviewed ? 'default' : 'outline'}
          disabled={draft === undefined || busy !== null}
          aria-busy={busy === 'save'}
          className="pointer-coarse:min-h-11"
        >
          {busy === 'save' && (
            <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
          )}
          {busy === 'save' ? 'Saving…' : 'Save'}
        </Button>
        {!saved.reviewed && (
          <Button
            type="button"
            disabled={busy !== null}
            aria-busy={busy === 'review'}
            className="pointer-coarse:min-h-11"
            onClick={() => void save(true)}
          >
            {busy === 'review' ? (
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
            ) : (
              <Check aria-hidden="true" />
            )}
            {busy === 'review' ? 'Saving…' : 'Save and mark reviewed'}
          </Button>
        )}
        {draft !== undefined && (
          <Button
            type="button"
            variant="ghost"
            disabled={busy !== null}
            className="pointer-coarse:min-h-11"
            onClick={() => onDraft(undefined)}
          >
            Discard changes
          </Button>
        )}
      </div>
      {saved.reviewed && (
        <p className="text-xs text-fg-muted">
          Reviewed: onboarding this database again keeps this table as it is.
        </p>
      )}
    </form>
  )
}

function TableHeading({
  id,
  ref,
  table,
  reviewed,
}: {
  id: string
  ref?: Ref<HTMLHeadingElement>
  table: string
  reviewed: boolean
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <h3 id={id} ref={ref} tabIndex={-1} className="rounded font-mono text-lg outline-none">
        {table}
      </h3>
      {reviewed ? (
        <Badge variant="secondary">
          <Check aria-hidden="true" />
          Reviewed
        </Badge>
      ) : (
        <Badge variant="outline">Draft</Badge>
      )}
    </div>
  )
}

type ColumnRowProps = {
  name: string
  type: string | undefined
  text: string
  /** Ticked? `undefined`: not known yet, so the box is locked. */
  personal: boolean | undefined
  piiHint: string
  onText: (text: string) => void
  onPersonal: (personal: boolean) => void
}

function ColumnRow({ name, type, text, personal, piiHint, onText, onPersonal }: ColumnRowProps) {
  const inputId = useId()
  return (
    <li className="grid gap-x-3 gap-y-1.5 sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)_auto] sm:items-center">
      <div className="min-w-0">
        <label htmlFor={inputId} className="block truncate font-mono text-sm font-medium">
          {name}
        </label>
        {type && <span className="block truncate text-xs text-fg-muted">{type}</span>}
      </div>
      <input
        id={inputId}
        maxLength={COLUMN_MAX}
        value={text}
        onChange={(event) => onText(event.target.value)}
        className={cn(FIELD, 'mt-0')}
      />
      <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-0">
        <input
          type="checkbox"
          checked={personal ?? false}
          disabled={personal === undefined}
          aria-describedby={piiHint}
          onChange={(event) => onPersonal(event.target.checked)}
          className="size-4 accent-[var(--primary)]"
        />
        Personal data <span className="sr-only">({name})</span>
      </label>
    </li>
  )
}

/** One table, read only: for the roles that may read the descriptions but not change them. */
export function TableView({
  table,
  saved,
  columns,
  personal,
}: {
  table: string
  saved: DictionaryTable
  columns: ColumnShown[]
  personal: string[] | undefined
}) {
  const headingId = useId()
  const flagged = new Set(personal ?? [])
  return (
    <section aria-labelledby={headingId} className="space-y-4 rounded-cell border bg-surface p-4">
      <TableHeading id={headingId} table={table} reviewed={saved.reviewed} />
      <p className="text-sm">
        {saved.description || <span className="text-fg-muted">No description yet.</span>}
      </p>
      <dl className="grid gap-x-4 gap-y-2 text-sm sm:grid-cols-[minmax(0,11rem)_minmax(0,1fr)]">
        {columns.map(({ name }) => (
          <div key={name} className="contents">
            <dt className="flex min-w-0 items-center gap-1.5 font-mono">
              <span className="truncate">{name}</span>
              {flagged.has(name) && (
                <span className="inline-flex shrink-0 items-center gap-1 font-sans text-xs text-fg-muted">
                  <ShieldAlert aria-hidden="true" className="size-3.5" />
                  Personal data
                </span>
              )}
            </dt>
            <dd className="text-fg-muted">{saved.columns[name] || 'No description yet.'}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
