import { LayoutGroup, m } from 'motion/react'
import type { ReactNode } from 'react'
import { NavLink } from 'react-router'

import type { EvalsReply } from '@/api/evals'
import { Skeleton } from '@/components/ui/skeleton'
import { LoadError } from '@/explorer/LoadError'
import { cn } from '@/lib/utils'
import { spring, useMotionSafe } from '@/motion'

import { useEvals } from './useEvals'
import { EVAL_VIEWS } from './views'

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
      {/* Signal Lab (U8.6): a mono label over the title, as on the other pages. */}
      <p aria-hidden="true" className="label-mono">
        EVALS LAB · MEASURED, NOT PROMISED
      </p>
      <h1 className="mt-1 text-3xl font-semibold tracking-tight [overflow-wrap:anywhere]">
        {title}
      </h1>
      <p className="mt-2 max-w-3xl text-fg-muted">{intro}</p>
      {/* The sub-menu: the open view is underlined in lime, and the line slides to the next view. */}
      <LayoutGroup id="evals-views">
        <nav aria-label="Evals lab views" className="mt-5 flex flex-wrap gap-x-1 border-b">
          {EVAL_VIEWS.map(({ to, label, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                cn(
                  'relative -mb-px inline-flex min-h-11 items-center rounded-t-lg px-3 text-sm transition-colors duration-150',
                  isActive ? 'font-semibold text-fg' : 'text-fg-muted hover:bg-muted hover:text-fg',
                )
              }
            >
              {({ isActive }) => (
                <>
                  {label}
                  {isActive && <ViewIndicator />}
                </>
              )}
            </NavLink>
          ))}
        </nav>
      </LayoutGroup>

      <div className="mt-6">
        {isPending ? (
          <Skeleton
            aria-hidden="true"
            className="h-64 w-full rounded-cell motion-reduce:animate-none"
          />
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

/**
 * The lime line under the open view. With motion on, it is one shared element (`layoutId`) that
 * slides from the old view to the new one; under reduced motion it is simply under the new one.
 * Below the text, never behind it, so the text's contrast is measured on the page's own colour.
 */
function ViewIndicator() {
  const { animate } = useMotionSafe()
  const line = 'absolute inset-x-2 bottom-0 h-0.5 rounded-full bg-signal'
  if (!animate) return <span aria-hidden="true" className={line} />
  return (
    <m.span
      aria-hidden="true"
      layoutId="evals-view-indicator"
      transition={spring.snappy}
      className={line}
    />
  )
}
