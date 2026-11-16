import { useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useId, useState } from 'react'
import { toast } from 'sonner'

import { asApiError, type ApiError } from '@/api/client'
import type { DatabaseId } from '@/api/explorer'
import {
  saveTableAccess,
  tableAccessKey,
  useTableAccess,
  type RestrictedRole,
  type TableAccessInfo,
} from '@/api/permissions'
import { useSession } from '@/api/session'
import { useConnections } from '@/api/settings'
import { FIELD } from '@/components/field'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { DEMO_NAME, useDatabase } from '@/layout/useDatabase'
import { cn } from '@/lib/utils'

import { FormError } from './FormError'

type Rule = string[] | null // null: every table, now and later

const ROLES: { role: RestrictedRole; plural: string }[] = [
  { role: 'analyst', plural: 'Analysts' },
  { role: 'viewer', plural: 'Viewers' },
]

/**
 * Settings → Permissions (U7.2, PLAN 5.2): per database, which tables analysts and viewers may
 * use, as a grid (a row per table, a column per role). "Every table" means new tables too; a
 * list hides a table added later until it is ticked. Each role's column is saved on its own (the
 * API sets one role's rule at a time). Admins only; admins themselves see every table.
 */
export default function PermissionsSettings() {
  const admin = useSession()?.user.role === 'admin'
  const headingId = useId()
  const pickerId = useId()
  const connections = useConnections({ enabled: admin })
  // Starts on the database the app is about; then this page's own choice.
  const current = useDatabase().id
  const [database, setDatabase] = useState<DatabaseId>(current)

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-xl">
        Permissions
      </h2>
      {!admin ? (
        <p className="mt-3 rounded-cell border bg-surface p-4 text-sm">
          Only an admin of this workspace can see and change who may use which tables.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-fg-muted">
            Which tables analysts and viewers may use: in their answers, the explorer and the
            suggestions. Admins always see every table. Which rows each member sees is set on the
            Members tab.
          </p>
          <div className="mt-4 max-w-xs">
            <label htmlFor={pickerId} className="text-sm font-medium">
              Database
            </label>
            <select
              id={pickerId}
              value={database ?? 'demo'}
              onChange={(event) =>
                setDatabase(event.target.value === 'demo' ? null : event.target.value)
              }
              className={FIELD}
            >
              <option value="demo">{DEMO_NAME}</option>
              {connections.data?.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.name}
                </option>
              ))}
            </select>
          </div>
          {/* A fresh grid per database: unsaved ticks belong to the database they were made on. */}
          <Access key={database ?? 'demo'} database={database} />
        </>
      )}
    </section>
  )
}

function Access({ database }: { database: DatabaseId }) {
  const access = useTableAccess(database)
  if (access.isPending) {
    return <Skeleton aria-hidden="true" className="mt-6 h-64 w-full motion-reduce:animate-none" />
  }
  if (access.isError) {
    return (
      <div className="mt-6">
        <LoadError
          error={access.error}
          onRetry={() => void access.refetch()}
          message="This database's tables can't be read right now."
        />
      </div>
    )
  }
  return <Grid database={database} info={access.data} />
}

function sameRule(a: Rule, b: Rule): boolean {
  if (a === null || b === null) return a === b
  const left = new Set(a)
  return left.size === new Set(b).size && b.every((table) => left.has(table))
}

function Grid({ database, info }: { database: DatabaseId; info: TableAccessInfo }) {
  const queryClient = useQueryClient()
  const [drafts, setDrafts] = useState<Partial<Record<RestrictedRole, Rule>>>({})
  const [busy, setBusy] = useState<RestrictedRole | null>(null)
  const [errors, setErrors] = useState<Partial<Record<RestrictedRole, ApiError>>>({})
  const captionId = useId()

  const rule = (role: RestrictedRole): Rule => (role in drafts ? drafts[role]! : info[role])
  const dirty = (role: RestrictedRole) => !sameRule(rule(role), info[role])

  function change(role: RestrictedRole, next: Rule) {
    setDrafts((all) => {
      if (sameRule(next, info[role])) {
        const { [role]: _dropped, ...rest } = all
        return rest
      }
      return { ...all, [role]: next }
    })
  }

  function tick(role: RestrictedRole, table: string, allowed: boolean) {
    const now = rule(role) ?? info.tables
    change(role, allowed ? [...now, table] : now.filter((one) => one !== table))
  }

  async function save(role: RestrictedRole, plural: string) {
    setBusy(role)
    setErrors(({ [role]: _old, ...rest }) => rest)
    try {
      const saved = await saveTableAccess(database, role, rule(role))
      queryClient.setQueryData(tableAccessKey(database), saved)
      setDrafts(({ [role]: _done, ...rest }) => rest)
      // What the role sees changes now: the explorer's lists are out of date for it.
      void queryClient.invalidateQueries({ queryKey: ['catalog', database] })
      void queryClient.invalidateQueries({ queryKey: ['semantic', database] })
      toast.success(`${plural}: saved.`)
    } catch (caught) {
      const failure = asApiError(caught)
      setErrors((all) => ({ ...all, [role]: failure }))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="mt-6 space-y-4">
      <div
        role="region"
        aria-labelledby={captionId}
        tabIndex={0}
        className="overflow-x-auto rounded-cell border bg-surface outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        <table className="w-full text-sm">
          <caption id={captionId} className="sr-only">
            Tables each role may use
          </caption>
          <thead className="bg-muted text-left">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                Table
              </th>
              {ROLES.map(({ role, plural }) => (
                <th key={role} scope="col" className="w-24 px-3 py-2 text-center font-medium">
                  {plural}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t bg-muted/40">
              <th scope="row" className="px-3 py-2 text-left font-medium">
                Every table
                <span className="block text-xs font-normal text-fg-muted">
                  New tables too, as they are added
                </span>
              </th>
              {ROLES.map(({ role, plural }) => (
                <td key={role} className="px-3 py-2 text-center">
                  <input
                    type="checkbox"
                    aria-label={`${plural} may use every table`}
                    checked={rule(role) === null}
                    // Unticked: a list of today's tables, so nothing changes until one is unticked.
                    onChange={(event) => change(role, event.target.checked ? null : info.tables)}
                    className="size-4 accent-[var(--primary)]"
                  />
                </td>
              ))}
            </tr>
            {info.tables.map((table) => (
              <tr key={table} className="border-t">
                <th scope="row" className="px-3 py-2 text-left font-mono font-normal">
                  {table}
                </th>
                {ROLES.map(({ role, plural }) => {
                  const now = rule(role)
                  return (
                    <td key={role} className="px-3 py-2 text-center">
                      <input
                        type="checkbox"
                        aria-label={`${plural} may use ${table}`}
                        checked={now === null || now.includes(table)}
                        disabled={now === null}
                        onChange={(event) => tick(role, table, event.target.checked)}
                        className="size-4 accent-[var(--primary)] disabled:opacity-50"
                      />
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        {ROLES.map(({ role, plural }) => {
          const now = rule(role)
          return (
            <div key={role} className="space-y-2">
              <Button
                type="button"
                disabled={!dirty(role) || busy !== null}
                aria-busy={busy === role}
                className="pointer-coarse:min-h-11"
                onClick={() => void save(role, plural)}
              >
                {busy === role && (
                  <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
                )}
                {busy === role ? 'Saving…' : `Save ${plural.toLowerCase()}`}
              </Button>
              <p className={cn('text-xs', now?.length === 0 ? 'text-danger' : 'text-fg-muted')}>
                {describe(now, info.tables.length, plural)}
                {dirty(role) && ' Not saved yet.'}
              </p>
              {errors[role] && <FormError error={errors[role]} />}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** A role's rule in words, under its Save button. */
function describe(rule: Rule, total: number, plural: string): string {
  if (rule === null) return `${plural} may use every table, new ones too.`
  if (rule.length === 0) return `${plural} may use no table: their questions here are refused.`
  return `${plural} may use ${rule.length} of ${total} tables; tables added later stay hidden.`
}
