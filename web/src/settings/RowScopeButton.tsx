import { useQueryClient } from '@tanstack/react-query'
import { Filter, Plus } from 'lucide-react'
import { useId, useState, type FormEvent } from 'react'
import { toast } from 'sonner'

import { ApiError, asApiError } from '@/api/client'
import { MEMBERS_QUERY, type MemberInfo } from '@/api/members'
import { DEMO_TAGS, saveRowScope, TAG_MAX_LENGTH, tagProblem, TAGS_MAX } from '@/api/permissions'
import { FIELD } from '@/components/field'
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
import { cn } from '@/lib/utils'

import { FormError } from './FormError'

/**
 * "Edit rows" on a member's row (U7.2, PLAN 5.3): every row, or only the rows with some tags.
 * The tags offered are the demo's regions and the ones already used in this workspace; another
 * can be typed. What a tag means is the database's row-security policies' business. Admins always
 * see every row, so their rows can't be set (no button).
 */
export function RowScopeButton({ member, known }: { member: MemberInfo; known: string[] }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [every, setEvery] = useState(member.row_scope === null)
  const [chosen, setChosen] = useState<string[]>(member.row_scope ?? [])
  const [offered, setOffered] = useState<string[]>([])
  const [typed, setTyped] = useState('')
  const [problem, setProblem] = useState<string | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  const ids = { every: useId(), some: useId(), tags: useId(), typed: useId(), problem: useId() }

  const tags = [...new Set([...DEMO_TAGS, ...known, ...chosen, ...offered])]

  function reset(nowOpen: boolean) {
    setOpen(nowOpen)
    if (!nowOpen) return
    // Each opening starts from what is saved.
    setEvery(member.row_scope === null)
    setChosen(member.row_scope ?? [])
    setOffered([])
    setTyped('')
    setProblem(null)
    setError(null)
  }

  function addTyped() {
    const tag = typed.trim()
    const why = tagProblem(tag)
    setProblem(why)
    if (why) return
    setOffered((all) => [...all, tag])
    setChosen((all) => (all.includes(tag) ? all : [...all, tag]))
    setEvery(false)
    setTyped('')
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!every && chosen.length === 0) {
      setProblem('Choose at least one tag, or "Every row".')
      return
    }
    if (chosen.length > TAGS_MAX) {
      setProblem(`At most ${TAGS_MAX} tags.`)
      return
    }
    setBusy(true)
    setError(null)
    try {
      const saved = await saveRowScope(member.user_id, every ? null : chosen)
      queryClient.setQueryData<MemberInfo[]>(MEMBERS_QUERY, (all) =>
        all?.map((one) =>
          one.user_id === saved.user_id ? { ...one, row_scope: saved.row_scope } : one,
        ),
      )
      setOpen(false)
      toast.success(
        saved.row_scope === null
          ? `${member.email} sees every row.`
          : `${member.email} sees the rows tagged ${saved.row_scope.join(', ')}.`,
      )
    } catch (caught) {
      setError(asApiError(caught))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={reset}>
      <DialogTrigger asChild>
        <Button type="button" variant="outline" className="pointer-coarse:min-h-11">
          <Filter aria-hidden="true" />
          Edit rows <span className="sr-only">of {member.email}</span>
        </Button>
      </DialogTrigger>
      <DialogContent>
        <form onSubmit={(event) => void onSubmit(event)} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Rows for {member.email}</DialogTitle>
            <DialogDescription>
              On the demo database, a tag is a Northwind sales region: they see the orders taken by
              that region&rsquo;s employees. On your own databases, your row-security policies
              decide what a tag means; a tag no policy knows shows no row.
            </DialogDescription>
          </DialogHeader>

          <fieldset className="space-y-2">
            <legend className="sr-only">Which rows</legend>
            <label htmlFor={ids.every} className="flex min-h-11 items-center gap-2 text-sm">
              <input
                id={ids.every}
                type="radio"
                name="rows"
                checked={every}
                onChange={() => setEvery(true)}
                className="size-4 accent-[var(--primary)]"
              />
              Every row
            </label>
            <label htmlFor={ids.some} className="flex min-h-11 items-center gap-2 text-sm">
              <input
                id={ids.some}
                type="radio"
                name="rows"
                checked={!every}
                onChange={() => setEvery(false)}
                className="size-4 accent-[var(--primary)]"
              />
              Only the rows tagged:
            </label>
          </fieldset>

          <fieldset
            aria-labelledby={ids.tags}
            disabled={every}
            className={cn('space-y-1 pl-6', every && 'opacity-50')}
          >
            <legend id={ids.tags} className="sr-only">
              Tags
            </legend>
            {tags.map((tag) => (
              <label key={tag} className="flex min-h-9 items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={chosen.includes(tag)}
                  onChange={(event) =>
                    setChosen((all) =>
                      event.target.checked ? [...all, tag] : all.filter((one) => one !== tag),
                    )
                  }
                  className="size-4 accent-[var(--primary)]"
                />
                {tag}
              </label>
            ))}
            <div className="flex items-end gap-2 pt-1">
              <div className="min-w-0 flex-1">
                <label htmlFor={ids.typed} className="text-xs font-medium">
                  Another tag
                </label>
                <input
                  id={ids.typed}
                  value={typed}
                  maxLength={TAG_MAX_LENGTH + 1}
                  onChange={(event) => setTyped(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter') return
                    event.preventDefault() // adds the tag; doesn't save the form
                    addTyped()
                  }}
                  aria-invalid={problem !== null || undefined}
                  aria-describedby={problem ? ids.problem : undefined}
                  className={cn(FIELD, 'h-9')}
                />
              </div>
              <Button type="button" variant="outline" onClick={addTyped}>
                <Plus aria-hidden="true" />
                Add
              </Button>
            </div>
          </fieldset>
          {problem && (
            <p id={ids.problem} role="alert" className="text-sm text-danger">
              {problem}
            </p>
          )}
          {error && <FormError error={error} />}

          <DialogFooter>
            <DialogClose asChild>
              <Button type="button" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button type="submit" disabled={busy} aria-busy={busy}>
              {busy ? 'Saving…' : 'Save rows'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
