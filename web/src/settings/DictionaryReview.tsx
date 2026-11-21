import { ArrowLeft, Check } from 'lucide-react'
import { useEffect, useId, useState, type ReactNode } from 'react'
import { Link, useBlocker, useParams, useSearchParams } from 'react-router'

import { ApiError } from '@/api/client'
import { isRunning, useDictionary, useOnboarding, type DataDictionary } from '@/api/onboarding'
import { useSession } from '@/api/session'
import { useConnections } from '@/api/settings'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { useExplorerData } from '@/explorer/useExplorerData'
import { FIELD } from '@/components/field'
import { cn } from '@/lib/utils'

import { flaggedIn, type TableDraft } from './dictionaryDraft'
import { TableForm, TableView, type ColumnShown } from './DictionaryTableForm'

/**
 * Settings → Databases → a database's descriptions (U7.1c): what onboarding drafted for each table
 * and column, to correct, flag personal data in, and mark reviewed (a reviewed table is never
 * drafted again). One table at a time, picked from a list (`?table=` in the URL), each saved on its
 * own. Edits to other tables wait as drafts until saved; leaving the page with drafts asks first.
 * The other roles read the descriptions of the tables they may use.
 */
export default function DictionaryReview() {
  const { id = '' } = useParams()
  const admin = useSession()?.user.role === 'admin'
  const connections = useConnections()
  const connection = connections.data?.find((one) => one.id === id)
  const dictionary = useDictionary(id)
  // Progress is admins' only (the API refuses the others): while a run goes on, edits are refused.
  const onboarding = useOnboarding(id, { enabled: admin })
  const running = admin && isRunning(onboarding.data?.status)
  const headingId = useId()

  const name = connection?.name ?? 'this database'
  return (
    <section aria-labelledby={headingId}>
      <Link
        to="/settings/databases"
        className="inline-flex min-h-11 items-center gap-1.5 text-sm text-fg-muted hover:text-fg"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        Back to databases
      </Link>
      <h2 id={headingId} className="mt-1 text-xl font-semibold tracking-tight">
        Descriptions of {name}
      </h2>

      <div className="mt-4">
        {connections.isSuccess && connection === undefined ? (
          <Missing>This workspace has no such database. It may have been deleted.</Missing>
        ) : dictionary.isPending ? (
          <Skeleton aria-hidden="true" className="h-64 w-full motion-reduce:animate-none" />
        ) : dictionary.isError ? (
          dictionary.error instanceof ApiError &&
          dictionary.error.code === 'dictionary_not_found' ? (
            <Missing>
              {name} has no descriptions yet: they are drafted when it is onboarded.{' '}
              {admin && (
                <Link to="/settings/databases" className="text-info underline underline-offset-4">
                  Onboard it on the Databases tab
                </Link>
              )}
            </Missing>
          ) : (
            <LoadError
              error={dictionary.error}
              onRetry={() => void dictionary.refetch()}
              message="The descriptions can't be loaded right now."
            />
          )
        ) : (
          <Review
            id={id}
            dictionary={dictionary.data}
            editable={admin && !running}
            running={running}
            onRunning={() => void onboarding.refetch()}
          />
        )}
      </div>
    </section>
  )
}

function Missing({ children }: { children: ReactNode }) {
  return <p className="rounded-cell border bg-surface p-4 text-sm">{children}</p>
}

type ReviewProps = {
  id: string
  dictionary: DataDictionary
  editable: boolean
  running: boolean
  onRunning: () => void
}

function Review({ id, dictionary, editable, running, onRunning }: ReviewProps) {
  const [params, setParams] = useSearchParams()
  const [drafts, setDrafts] = useState<Record<string, TableDraft>>({})
  // Column order and types come from the database itself: the dictionary's columns are stored as
  // JSON, whose key order isn't the table's. The overview has the personal-data flags in force.
  const explorer = useExplorerData(id)
  const pickerId = useId()

  const names = Object.keys(dictionary.tables).sort()
  const asked = params.get('table')
  const table = asked !== null && asked in dictionary.tables ? asked : names[0]
  const reviewed = names.filter((name) => dictionary.tables[name]?.reviewed).length

  const unsaved = Object.keys(drafts)

  function pick(name: string) {
    setParams({ table: name }, { replace: true })
  }

  function setDraft(name: string, draft: TableDraft | undefined) {
    setDrafts((all) => {
      const { [name]: _dropped, ...rest } = all
      return draft === undefined ? rest : { ...rest, [name]: draft }
    })
  }

  if (table === undefined) {
    return <Missing>Onboarding found no table it could describe.</Missing>
  }
  const saved = dictionary.tables[table]!
  const catalogTable = explorer.catalog?.tables.find(({ name }) => name === table)
  const columns: ColumnShown[] = orderedColumns(Object.keys(saved.columns), catalogTable?.columns)

  return (
    <div className="space-y-4">
      <LeaveGuard unsaved={unsaved} />
      <p className="text-sm text-fg-muted">
        {reviewed} of {names.length} {names.length === 1 ? 'table' : 'tables'} reviewed
        {dictionary.generated_by && ` · drafted by ${dictionary.generated_by}`}. A reviewed table is
        kept as it is when the database is onboarded again.
      </p>
      {running && (
        <p
          role="note"
          className="rounded-cell border bg-surface px-4 py-2.5 text-sm shadow-[inset_2px_0_0_var(--info)]"
        >
          Onboarding is running: the descriptions can be edited once it has finished.
        </p>
      )}

      <div className="flex flex-col gap-4 md:flex-row">
        {/* A phone has no room for the list beside the form: a menu instead, same URL. */}
        <div className="md:hidden">
          <label htmlFor={pickerId} className="text-sm font-medium">
            Table
          </label>
          <select
            id={pickerId}
            value={table}
            onChange={(event) => pick(event.target.value)}
            className={FIELD}
          >
            {names.map((name) => (
              <option key={name} value={name}>
                {name}
                {dictionary.tables[name]?.reviewed ? ' (reviewed)' : ''}
                {name in drafts ? ' (unsaved)' : ''}
              </option>
            ))}
          </select>
        </div>
        <nav aria-label="Tables" className="hidden md:block md:w-52 md:shrink-0">
          <ul className="space-y-0.5">
            {names.map((name) => (
              <li key={name}>
                <Link
                  to={{ search: `?table=${encodeURIComponent(name)}` }}
                  replace
                  aria-current={name === table ? 'page' : undefined}
                  className={cn(
                    'flex min-h-9 items-center gap-2 rounded-lg px-2.5 font-mono text-sm transition-colors',
                    name === table
                      ? 'bg-muted font-medium text-fg'
                      : 'text-fg-muted hover:bg-muted hover:text-fg',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{name}</span>
                  {name in drafts && (
                    <span className="font-sans text-xs text-info">
                      <span aria-hidden="true">●</span>
                      <span className="sr-only">, unsaved changes</span>
                    </span>
                  )}
                  {dictionary.tables[name]?.reviewed && (
                    <>
                      <Check aria-hidden="true" className="size-4 shrink-0 text-signal" />
                      <span className="sr-only">, reviewed</span>
                    </>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <div className="min-w-0 flex-1">
          {editable ? (
            <TableForm
              // A fresh form per table: its busy state and error belong to the table.
              key={table}
              id={id}
              table={table}
              saved={saved}
              draft={drafts[table]}
              columns={columns}
              guessed={flaggedIn(explorer.semantic, table)}
              flagsFailed={explorer.error !== null && explorer.semantic === undefined}
              onDraft={(draft) => setDraft(table, draft)}
              onRunning={onRunning}
            />
          ) : (
            <TableView
              table={table}
              saved={saved}
              columns={columns}
              personal={saved.pii ?? flaggedIn(explorer.semantic, table)}
            />
          )}
        </div>
      </div>
    </div>
  )
}

/** The dictionary's columns in the table's own order (with types), then any the catalog lacks. */
function orderedColumns(
  described: string[],
  inTable: { name: string; data_type: string }[] | undefined,
): ColumnShown[] {
  const known = new Set(described)
  const ordered = (inTable ?? [])
    .filter(({ name }) => known.has(name))
    .map(({ name, data_type }) => ({ name, type: data_type }))
  const placed = new Set(ordered.map(({ name }) => name))
  return [...ordered, ...described.filter((name) => !placed.has(name)).map((name) => ({ name }))]
}

/**
 * With unsaved drafts, leaving the page asks first: another page of the app (a dialog), or closing
 * or reloading the tab (the browser's own question). Picking another table isn't leaving.
 */
function LeaveGuard({ unsaved }: { unsaved: string[] }) {
  const dirty = unsaved.length > 0
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      dirty && currentLocation.pathname !== nextLocation.pathname,
  )
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  const tables = unsaved.length === 1 ? unsaved[0] : `${unsaved.length} tables`
  return (
    <Dialog
      open={blocker.state === 'blocked'}
      onOpenChange={(open) => {
        if (!open && blocker.state === 'blocked') blocker.reset()
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Leave without saving?</DialogTitle>
          <DialogDescription>
            Your changes to {tables} aren&rsquo;t saved. Leaving drops them.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => blocker.reset?.()}>
            Stay
          </Button>
          <Button type="button" variant="destructive" onClick={() => blocker.proceed?.()}>
            Leave
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
