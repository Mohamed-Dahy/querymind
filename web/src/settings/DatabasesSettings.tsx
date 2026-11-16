import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Loader2, RefreshCw } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type RefObject } from 'react'
import { Link } from 'react-router'
import { toast } from 'sonner'

import { asApiError, type ApiError } from '@/api/client'
import { CONVERSATIONS_KEY } from '@/api/conversations'
import { useSession } from '@/api/session'
import {
  CONNECTIONS_QUERY,
  deleteConnection,
  saveConnection,
  testConnection,
  useConnections,
  useLLMKey,
  type ConnectionInfo,
  type SslMode,
} from '@/api/settings'
import { SqlCode } from '@/canvas/SqlView'
import { formatWait } from '@/canvas/wait'
import { FIELD } from '@/components/field'
import { SubmitButton } from '@/components/SubmitButton'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { useSecondsLeft } from '@/lib/useSecondsLeft'

import { ConfirmButton } from './ConfirmButton'
import { FormError } from './FormError'

const CHECKED = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' })

// What each TLS mode means, in words. The last two are refused unless the server runs with
// ALLOW_PRIVATE_DATABASES (development: the Docker database), so they say so.
const SSL_MODES: { value: SslMode; label: string }[] = [
  { value: 'verify-full', label: 'Encrypted, certificate checked (recommended)' },
  { value: 'require', label: 'Encrypted, certificate not checked (self-signed)' },
  { value: 'prefer', label: 'Encrypted if the server offers it (development only)' },
  { value: 'disable', label: 'Not encrypted (development only)' },
]

// A login that can only read: the same grants the read-only check is tested with
// (tests/integration/test_remote_databases.py). The last line is for PostgreSQL 14 and older,
// where everyone may create tables in `public` (ADR 012): the check refuses that.
const READ_ONLY_RECIPE = `CREATE ROLE querymind_reader LOGIN PASSWORD 'a-long-random-password';
GRANT CONNECT ON DATABASE your_database TO querymind_reader;
GRANT USAGE ON SCHEMA public TO querymind_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO querymind_reader;
-- PostgreSQL 14 and older only:
REVOKE CREATE ON SCHEMA public FROM PUBLIC;`

/**
 * Settings → Databases (U7.1b part 2): the workspace's own PostgreSQL databases. Every member sees
 * the list; admins add one (QueryMind connects and refuses a login that can change data), test it
 * again, or delete it with its conversations. The password goes in once and never comes back.
 */
export default function DatabasesSettings() {
  const connections = useConnections()
  const key = useLLMKey()
  const admin = useSession()?.user.role === 'admin'
  const headingId = useId()
  // Where focus goes after a delete: the database's row is gone by then.
  const heading = useRef<HTMLHeadingElement>(null)

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} ref={heading} tabIndex={-1} className="rounded-lg text-xl outline-none">
        Databases
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Your own PostgreSQL databases, to ask about. QueryMind connects with a login that can only
        read, and refuses one that could change your data.
      </p>

      <div className="mt-4 space-y-6">
        {connections.isPending ? (
          <Skeleton aria-hidden="true" className="h-40 w-full motion-reduce:animate-none" />
        ) : connections.isError ? (
          <LoadError
            error={connections.error}
            onRetry={() => void connections.refetch()}
            message="Your databases can't be listed right now."
          />
        ) : (
          <>
            {/* Asking needs the workspace's own key (5.11): say so before anyone tries. */}
            {key.data === null && connections.data.length > 0 && (
              <p className="rounded-lg border bg-muted px-3 py-2 text-sm">
                Questions about your own databases run on your own LLM key.{' '}
                <Link to="/settings/key" className="text-info underline underline-offset-4">
                  {admin ? 'Save one' : 'An admin can save one'} in the LLM key tab
                </Link>
                .
              </p>
            )}
            <DatabaseList connections={connections.data} admin={admin} focusAfterDelete={heading} />
            {admin ? (
              <AddDatabase />
            ) : (
              <p className="text-sm text-fg-muted">
                Only an admin of this workspace can add, test or delete databases.
              </p>
            )}
          </>
        )}
      </div>
    </section>
  )
}

type DatabaseListProps = {
  connections: ConnectionInfo[]
  admin: boolean
  focusAfterDelete: RefObject<HTMLElement | null>
}

function DatabaseList({ connections, admin, focusAfterDelete }: DatabaseListProps) {
  if (connections.length === 0) {
    return (
      <div className="rounded-cell border bg-surface p-4 text-sm">
        <p className="font-medium">No database yet</p>
        <p className="mt-1 text-fg-muted">
          Questions use the demo databases (Northwind and Chinook) until one is added.
        </p>
      </div>
    )
  }
  return (
    <ul aria-label="Your databases" className="space-y-3">
      {connections.map((connection) => (
        <DatabaseRow
          key={connection.id}
          connection={connection}
          admin={admin}
          focusAfterDelete={focusAfterDelete}
        />
      ))}
    </ul>
  )
}

/** One database: where it is, when it was last found safe, and the admin's two actions. */
function DatabaseRow({
  connection,
  admin,
  focusAfterDelete,
}: {
  connection: ConnectionInfo
  admin: boolean
  focusAfterDelete: RefObject<HTMLElement | null>
}) {
  const queryClient = useQueryClient()
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number>()
  const nameId = useId()
  const errorId = useId()
  const sslLabel = SSL_MODES.find(({ value }) => value === connection.sslmode)?.label

  async function testAgain() {
    setTesting(true)
    setError(null)
    try {
      const checked = await testConnection(connection.id)
      queryClient.setQueryData<ConnectionInfo[]>(CONNECTIONS_QUERY, (all) =>
        all?.map((one) => (one.id === checked.id ? checked : one)),
      )
      toast.success(`${connection.name}: still reachable, and the login can still only read.`)
    } catch (caught) {
      const failure = asApiError(caught)
      setError(failure)
      if (failure.retryAfterSeconds !== undefined) {
        setRetryAt(Date.now() + failure.retryAfterSeconds * 1000)
      }
    } finally {
      setTesting(false)
    }
  }

  return (
    <li aria-labelledby={nameId} className="rounded-cell border bg-surface p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p id={nameId} className="font-medium">
            {connection.name}
          </p>
          <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm">
            <dt className="text-fg-muted">Where</dt>
            <dd className="font-mono break-all">
              {connection.user}@{connection.host}:{connection.port}/{connection.dbname}
            </dd>
            <dt className="text-fg-muted">Connection</dt>
            <dd>{sslLabel ?? connection.sslmode}</dd>
            <dt className="text-fg-muted">Checked</dt>
            <dd className="flex items-center gap-1">
              <CheckCircle2 aria-hidden="true" className="size-4 shrink-0 text-signal" />
              <span>
                Reachable and read-only,{' '}
                <time dateTime={connection.checked_at}>
                  {CHECKED.format(new Date(connection.checked_at))}
                </time>
              </span>
            </dd>
          </dl>
        </div>
        {admin && (
          <div className="flex flex-wrap gap-2">
            {/* Grants can change after saving: this checks again, as on the first save. */}
            <Button
              type="button"
              variant="outline"
              disabled={testing}
              aria-busy={testing}
              aria-describedby={error ? errorId : undefined}
              className="pointer-coarse:min-h-11"
              onClick={() => void testAgain()}
            >
              {testing ? (
                <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              ) : (
                <RefreshCw aria-hidden="true" />
              )}
              {testing ? 'Testing…' : 'Test again'}{' '}
              {/* Which one, for a screen reader's list of buttons. The space is outside the span:
                  an accessible name trims each part, so " Sales" inside would read "againSales". */}
              <span className="sr-only">{connection.name}</span>
            </Button>
            <ConfirmButton
              label="Delete"
              which={connection.name}
              busyLabel="Deleting…"
              title={`Delete ${connection.name}?`}
              description={
                <>
                  QueryMind forgets this database and its password, and every conversation about it
                  is deleted too. Your database itself isn&rsquo;t touched.
                </>
              }
              onConfirm={() => deleteConnection(connection.id)}
              onDone={() => {
                queryClient.setQueryData<ConnectionInfo[]>(CONNECTIONS_QUERY, (all) =>
                  all?.filter((one) => one.id !== connection.id),
                )
                // Its conversations went with it (the database cascades): the history must too.
                void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
                focusAfterDelete.current?.focus()
                toast.success(`${connection.name} deleted, with its conversations.`)
              }}
            />
          </div>
        )}
      </div>
      {error && (
        <div className="mt-3">
          <FormError id={errorId} error={error} />
          {retryAt !== undefined && <RetryNote key={retryAt} retryAt={retryAt} />}
        </div>
      )}
    </li>
  )
}

/** After a "too many checks": when testing again makes sense. Keyed by the wait where it's used,
 * so it counts from the 429 (useSecondsLeft counts from when it appears). */
function RetryNote({ retryAt }: { retryAt: number }) {
  const secondsLeft = useSecondsLeft(retryAt)
  return (
    <p className="mt-1.5 text-xs text-fg-muted tabular-nums">
      {secondsLeft > 0
        ? `You can test again in ${formatWait(secondsLeft)}.`
        : 'You can test again now.'}
    </p>
  )
}

/**
 * Add a database. QueryMind connects (a few seconds), checks the login can only read, and only
 * then saves it. The password is read from its field on submit and the form is cleared after a
 * save: it isn't kept in React state or the query cache (like the LLM key).
 */
function AddDatabase() {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number>()
  const ids = {
    heading: useId(),
    name: useId(),
    host: useId(),
    port: useId(),
    dbname: useId(),
    user: useId(),
    password: useId(),
    sslmode: useId(),
    sslHint: useId(),
    error: useId(),
    recipe: useId(),
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const field = (name: string) => String(new FormData(form).get(name) ?? '').trim()
    setBusy(true)
    setError(null)
    try {
      const saved = await saveConnection({
        name: field('name'),
        host: field('host'),
        port: Number(field('port')),
        dbname: field('dbname'),
        user: field('user'),
        password: String(new FormData(form).get('password') ?? ''), // as typed: spaces count
        sslmode: field('sslmode') as SslMode,
      })
      form.reset() // the password too: nothing of it stays on the page
      queryClient.setQueryData<ConnectionInfo[]>(CONNECTIONS_QUERY, (all) => [
        ...(all ?? []),
        saved,
      ])
      toast.success(`${saved.name} added: reachable, and the login can only read.`)
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

  const described = (...hints: string[]) =>
    [...hints, error && ids.error].filter(Boolean).join(' ') || undefined

  return (
    <form
      aria-labelledby={ids.heading}
      onSubmit={(event) => void onSubmit(event)}
      className="space-y-4 rounded-cell border bg-surface p-4"
    >
      <div>
        <h3 id={ids.heading} className="font-medium">
          Add a database
        </h3>
        <p className="mt-1 text-sm text-fg-muted">
          PostgreSQL on the public internet. Use a login that can only read: QueryMind checks, and
          refuses one that could change anything.
        </p>
      </div>

      <details className="rounded-lg border bg-muted px-3 py-2 text-sm">
        <summary className="min-h-11 cursor-pointer content-center font-medium">
          How to make a read-only login
        </summary>
        <p id={ids.recipe} className="mt-2 text-fg-muted">
          Run this as your database&rsquo;s owner, with your own database name and password. New
          tables need their own <code>GRANT SELECT</code> later.
        </p>
        <div className="mt-2 overflow-x-auto">
          <SqlCode sql={READ_ONLY_RECIPE} />
        </div>
      </details>

      <div>
        <label htmlFor={ids.name} className="text-sm font-medium">
          Name
        </label>
        <input
          id={ids.name}
          name="name"
          required
          maxLength={100}
          placeholder="Sales"
          aria-invalid={error?.code === 'connection_name_taken' || undefined}
          aria-describedby={described()}
          className={FIELD}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_8rem]">
        <div>
          <label htmlFor={ids.host} className="text-sm font-medium">
            Host
          </label>
          <input
            id={ids.host}
            name="host"
            required
            maxLength={253}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="db.example.com"
            aria-describedby={described()}
            className={`${FIELD} font-mono`}
          />
        </div>
        <div>
          <label htmlFor={ids.port} className="text-sm font-medium">
            Port
          </label>
          <input
            id={ids.port}
            name="port"
            type="number"
            required
            min={1}
            max={65535}
            defaultValue={5432}
            className={`${FIELD} font-mono`}
          />
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label htmlFor={ids.dbname} className="text-sm font-medium">
            Database
          </label>
          <input
            id={ids.dbname}
            name="dbname"
            required
            maxLength={63}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="sales"
            className={`${FIELD} font-mono`}
          />
        </div>
        <div>
          <label htmlFor={ids.user} className="text-sm font-medium">
            User
          </label>
          <input
            id={ids.user}
            name="user"
            required
            maxLength={63}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="querymind_reader"
            aria-invalid={error?.code === 'connection_writable' || undefined}
            aria-describedby={described(ids.recipe)}
            className={`${FIELD} font-mono`}
          />
        </div>
      </div>

      <div>
        <label htmlFor={ids.password} className="text-sm font-medium">
          Password
        </label>
        <input
          id={ids.password}
          name="password"
          type="password"
          required
          maxLength={1024}
          // The database's password, not this site's: the browser must not save or fill it.
          autoComplete="off"
          aria-invalid={error?.code === 'connection_login_failed' || undefined}
          aria-describedby={described()}
          className={FIELD}
        />
      </div>

      <div>
        <label htmlFor={ids.sslmode} className="text-sm font-medium">
          Encryption
        </label>
        <select
          id={ids.sslmode}
          name="sslmode"
          defaultValue="verify-full"
          aria-invalid={error?.code === 'connection_tls_failed' || undefined}
          aria-describedby={described(ids.sslHint)}
          className={FIELD}
        >
          {SSL_MODES.map(({ value, label }) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <p id={ids.sslHint} className="mt-1.5 text-xs text-fg-muted">
          Most hosted databases have a checked certificate. Choose &ldquo;not checked&rdquo; only if
          yours uses a self-signed one.
        </p>
      </div>

      {error && <FormError id={ids.error} error={error} />}

      <SubmitButton
        label="Check and add"
        busyLabel="Checking the database…"
        busy={busy}
        retryAt={retryAt}
      />
    </form>
  )
}
