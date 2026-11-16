import { useQueryClient } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { useId, useRef, useState, type FormEvent, type RefObject } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { asApiError, type ApiError } from '@/api/client'
import {
  addMember,
  changeRole,
  MEMBERS_QUERY,
  removeMember,
  useMembers,
  type MemberInfo,
  type Role,
} from '@/api/members'
import { useWorkspaces } from '@/api/auth'
import { refreshSession, updateUser, useSession } from '@/api/session'
import { FIELD } from '@/components/field'
import { SubmitButton } from '@/components/SubmitButton'
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
import { cn } from '@/lib/utils'

import { ConfirmButton } from './ConfirmButton'
import { FormError } from './FormError'

const JOINED = new Intl.DateTimeFormat('en', { dateStyle: 'medium' })

// What each role may do, in words (5.1, 5.2, 5.4, 5.12): shown under the add form's choice.
// Analysts and viewers differ only in the tables and rows admins allow each (Step 6).
const ROLES: { value: Role; label: string; does: string }[] = [
  {
    value: 'admin',
    label: 'Admin',
    does: 'manages the key, databases and members, and sees personal data',
  },
  { value: 'analyst', label: 'Analyst', does: 'asks questions, on the tables allowed to analysts' },
  { value: 'viewer', label: 'Viewer', does: 'asks questions, on the tables allowed to viewers' },
]
const ROLE_LABEL = Object.fromEntries(ROLES.map(({ value, label }) => [value, label])) as Record<
  Role,
  string
>

/**
 * Settings → Members (U7.1d): who can use this workspace. Admins add someone who has signed up
 * (by email, with a role), change a role with the select on their row (it saves at once), or
 * remove them. A change applies at the member's next request. Demoting or removing yourself asks
 * first: you lose this page. Admins only, like the API; another role reaching the URL is told so.
 */
export default function MembersSettings() {
  const session = useSession()
  const admin = session?.user.role === 'admin'
  const members = useMembers({ enabled: admin })
  const headingId = useId()
  const heading = useRef<HTMLHeadingElement>(null)

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} ref={heading} tabIndex={-1} className="rounded-lg text-xl outline-none">
        Members
      </h2>
      {!admin ? (
        <p className="mt-3 rounded-cell border bg-surface p-4 text-sm">
          Only an admin of this workspace can see and change its members.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-fg-muted">
            Who can use this workspace, and what they may do. A change applies at their next
            request.
          </p>
          <div className="mt-4 space-y-6">
            {members.isPending ? (
              <Skeleton aria-hidden="true" className="h-40 w-full motion-reduce:animate-none" />
            ) : members.isError ? (
              <LoadError
                error={members.error}
                onRetry={() => void members.refetch()}
                message="The members can't be listed right now."
              />
            ) : (
              <ul aria-label="Members" className="divide-y rounded-cell border bg-surface">
                {members.data.map((member) => (
                  <MemberRow
                    key={member.user_id}
                    member={member}
                    you={member.user_id === session.user.id}
                    focusAfterRemove={heading}
                  />
                ))}
              </ul>
            )}
            <AddMember />
          </div>
        </>
      )}
    </section>
  )
}

function MemberRow({
  member,
  you,
  focusAfterRemove,
}: {
  member: MemberInfo
  you: boolean
  focusAfterRemove: RefObject<HTMLElement | null>
}) {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const session = useSession()
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  // Demoting yourself: the role waiting for "yes" in the dialog.
  const [asking, setAsking] = useState<Role | null>(null)
  const ids = { email: useId(), role: useId(), error: useId() }
  // Leaving your only workspace would lock you out: the API then refuses your login altogether
  // (seen live, 2026-11-16). So "Leave" needs another workspace to land in.
  const workspaces = useWorkspaces({ enabled: you })
  const canLeave = !you || (workspaces.data?.length ?? 0) > 1

  async function save(role: Role) {
    setSaving(true)
    setError(null)
    try {
      const changed = await changeRole(member.user_id, role)
      queryClient.setQueryData<MemberInfo[]>(MEMBERS_QUERY, (all) =>
        all?.map((one) => (one.user_id === changed.user_id ? changed : one)),
      )
      if (you && session) {
        // No longer an admin: this page and the admin tabs close. The API already reads the new
        // role; the session follows it now instead of at the next check.
        updateUser({ ...session.user, role: changed.role })
        toast.success(`You are ${ROLE_LABEL[changed.role].toLowerCase()} in this workspace now.`)
        void navigate('/settings/key')
        return
      }
      toast.success(`${member.email} is ${ROLE_LABEL[changed.role].toLowerCase()} now.`)
    } catch (caught) {
      // The select shows the saved role again (it follows the list, which didn't change).
      setError(asApiError(caught))
    } finally {
      setSaving(false)
    }
  }

  function choose(role: Role) {
    if (role === member.role) return
    if (you && role !== 'admin') setAsking(role)
    else void save(role)
  }

  const scope = member.row_scope === null ? 'All rows' : member.row_scope.join(', ')
  return (
    <li aria-labelledby={ids.email} className="p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1 basis-56">
          <p id={ids.email} className="truncate font-medium">
            {member.email}
            {you && <span className="font-normal text-fg-muted"> (you)</span>}
          </p>
          <p className="mt-0.5 text-sm text-fg-muted">
            <span className="sr-only">Rows: </span>
            {scope} · joined{' '}
            <time dateTime={member.added_at}>{JOINED.format(new Date(member.added_at))}</time>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor={ids.role} className="sr-only">
            Role of {member.email}
          </label>
          <select
            id={ids.role}
            value={member.role}
            disabled={saving}
            aria-busy={saving}
            aria-describedby={error ? ids.error : undefined}
            onChange={(event) => choose(event.target.value as Role)}
            className={cn(FIELD, 'mt-0 w-32')}
          >
            {ROLES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {saving && (
            <Loader2
              aria-hidden="true"
              className="size-4 animate-spin motion-reduce:animate-none"
            />
          )}
          {canLeave ? (
            <ConfirmButton
              label={you ? 'Leave' : 'Remove'}
              which={you ? undefined : member.email}
              busyLabel={you ? 'Leaving…' : 'Removing…'}
              title={you ? 'Leave this workspace?' : `Remove ${member.email}?`}
              description={
                you ? (
                  <>
                    You lose access to this workspace&rsquo;s databases and conversations, and move
                    to another of your workspaces. An admin can add you again.
                  </>
                ) : (
                  <>
                    They lose access to this workspace at their next request. Their account and
                    their own workspace stay; you can add them again.
                  </>
                )
              }
              onConfirm={() => removeMember(member.user_id)}
              onDone={() => {
                if (you) {
                  // The refresh lands in another workspace: the app resets for it (useWorkspaceChange).
                  void refreshSession()
                  return
                }
                queryClient.setQueryData<MemberInfo[]>(MEMBERS_QUERY, (all) =>
                  all?.filter((one) => one.user_id !== member.user_id),
                )
                focusAfterRemove.current?.focus()
                toast.success(`${member.email} removed.`)
              }}
            />
          ) : (
            <p className="max-w-40 text-xs text-fg-muted">
              Your only workspace: you can&rsquo;t leave it.
            </p>
          )}
        </div>
      </div>
      {error && (
        <div className="mt-3">
          <FormError id={ids.error} error={error} />
        </div>
      )}
      <Dialog open={asking !== null} onOpenChange={(open) => !open && setAsking(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              Make yourself {asking ? ROLE_LABEL[asking].toLowerCase() : ''}?
            </DialogTitle>
            <DialogDescription>
              You stop being an admin here: you can no longer change the key, the databases or the
              members. Another admin can make you one again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setAsking(null)}>
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={() => {
                const role = asking
                setAsking(null)
                if (role) void save(role)
              }}
            >
              Make me {asking ? ROLE_LABEL[asking].toLowerCase() : ''}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </li>
  )
}

/**
 * Add someone who has signed up, by email, with a role. There are no invitations (5.12): someone
 * without an account is told to sign up first. Failed tries count towards a limit, like a login's.
 */
function AddMember() {
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [retryAt, setRetryAt] = useState<number>()
  const ids = { heading: useId(), email: useId(), role: useId(), roleHint: useId(), error: useId() }
  const [role, setRole] = useState<Role>('viewer')

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    const form = event.currentTarget
    const email = String(new FormData(form).get('email') ?? '').trim()
    setBusy(true)
    setError(null)
    try {
      const added = await addMember(email, role)
      queryClient.setQueryData<MemberInfo[]>(MEMBERS_QUERY, (all) => [...(all ?? []), added])
      form.reset()
      setRole('viewer')
      toast.success(`${added.email} added as ${ROLE_LABEL[added.role].toLowerCase()}.`)
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

  const does = ROLES.find(({ value }) => value === role)?.does
  return (
    <form
      aria-labelledby={ids.heading}
      onSubmit={(event) => void onSubmit(event)}
      className="space-y-4 rounded-cell border bg-surface p-4"
    >
      <div>
        <h3 id={ids.heading} className="font-medium">
          Add a member
        </h3>
        <p className="mt-1 text-sm text-fg-muted">
          Someone who has signed up to QueryMind: they then find this workspace in their account
          menu.
        </p>
      </div>
      <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <div>
          <label htmlFor={ids.email} className="text-sm font-medium">
            Email
          </label>
          <input
            id={ids.email}
            name="email"
            type="email"
            required
            maxLength={320}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="ana@example.com"
            aria-invalid={
              error?.code === 'account_not_found' || error?.code === 'already_member' || undefined
            }
            aria-describedby={error ? ids.error : undefined}
            className={FIELD}
          />
        </div>
        <div>
          <label htmlFor={ids.role} className="text-sm font-medium">
            Role
          </label>
          <select
            id={ids.role}
            value={role}
            onChange={(event) => setRole(event.target.value as Role)}
            aria-describedby={ids.roleHint}
            className={FIELD}
          >
            {ROLES.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
      </div>
      <p id={ids.roleHint} className="-mt-2 text-xs text-fg-muted">
        {ROLE_LABEL[role]}: {does}.
      </p>
      {error && <FormError id={ids.error} error={error} />}
      <SubmitButton label="Add" busyLabel="Adding…" busy={busy} retryAt={retryAt} />
    </form>
  )
}
