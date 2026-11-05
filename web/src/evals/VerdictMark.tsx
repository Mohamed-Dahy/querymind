import { Check, Circle, Minus, X, type LucideIcon } from 'lucide-react'

import { cn } from '@/lib/utils'

import type { Verdict } from './questionRows'

// A shape for each state, so the matrix reads with colour removed.
const MARKS: Record<Verdict, { icon: LucideIcon; colour: string }> = {
  passed: { icon: Check, colour: 'text-signal' },
  failed: { icon: X, colour: 'text-danger' },
  'not-scored': { icon: Minus, colour: 'text-warn' },
  'not-run': { icon: Circle, colour: 'text-fg-muted' },
}

export function VerdictMark({ verdict, className }: { verdict: Verdict; className?: string }) {
  const { icon: Icon, colour } = MARKS[verdict]
  return (
    <Icon
      aria-hidden="true"
      data-verdict={verdict}
      strokeWidth={verdict === 'not-run' ? 1.5 : 3}
      className={cn('shrink-0', verdict === 'not-run' ? 'size-2.5' : 'size-4', colour, className)}
    />
  )
}
