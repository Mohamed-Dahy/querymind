import type { ReactNode } from 'react'
import { NavLink } from 'react-router'

import type { EvalsReply } from '@/api/evals'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { cn } from '@/lib/utils'

import { useEvals } from './useEvals'

const VIEWS = [
  { to: '/evals', label: 'Scoreboard', end: true },
  { to: '/evals/ablations', label: 'What each part adds', end: false },
  { to: '/evals/models', label: 'Models', end: false },
  { to: '/evals/questions', label: 'Questions', end: false },
]

type EvalsFrameProps = {
  title: string
  intro: string
  children: (data: EvalsReply) => ReactNode
}

/** What every evals view shares: its heading, the sub-menu, and the loading and failed states. */
export function EvalsFrame({ title, intro, children }: EvalsFrameProps) {
  const { data, error, isPending, refetch } = useEvals()
  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <h1 className="text-2xl [overflow-wrap:anywhere]">{title}</h1>
      <p className="mt-2 text-fg-muted">{intro}</p>
      <nav aria-label="Evals lab views" className="mt-4 flex flex-wrap gap-2">
        {VIEWS.map(({ to, label, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive }) =>
              cn(
                'inline-flex min-h-11 items-center rounded-lg border px-3 text-sm',
                isActive ? 'border-input bg-muted font-semibold' : 'text-fg-muted hover:bg-muted',
              )
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <div className="mt-6">
        {isPending ? (
          <Skeleton aria-hidden="true" className="h-64 w-full motion-reduce:animate-none" />
        ) : data === undefined ? (
          <LoadError
            error={error}
            onRetry={() => void refetch()}
            message="The saved results can't be read on this server. The rest of the app still works."
          />
        ) : (
          children(data)
        )}
      </div>
    </div>
  )
}
