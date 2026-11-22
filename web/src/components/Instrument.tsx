import type { ReactNode } from 'react'

import { cn } from '@/lib/utils'

/**
 * Signal Lab's instrument details (U9, ADR 027), shared by the landing page, the login pages and the
 * canvas. All decoration: hidden from screen readers, never behind text, and drawn with hairlines
 * (no glow, blur or gradient a contrast checker can't measure).
 */

/**
 * Four crop marks just outside a panel's corners, like the registration marks on a print sheet. The
 * panel needs `relative`; the marks sit 8 px outside it, so it needs that much room around it.
 */
export function CropMarks({ className }: { className?: string }) {
  const mark = 'absolute size-3 border-fg-muted/70'
  return (
    <span aria-hidden="true" className={cn('pointer-events-none absolute -inset-2', className)}>
      <span className={cn(mark, 'top-0 left-0 border-t border-l')} />
      <span className={cn(mark, 'top-0 right-0 border-t border-r')} />
      <span className={cn(mark, 'bottom-0 left-0 border-b border-l')} />
      <span className={cn(mark, 'right-0 bottom-0 border-r border-b')} />
    </span>
  )
}

/**
 * A model plate, like the one riveted to an instrument: a code in a lime box, then what it is.
 * "QM-1 | AI DATA ANALYST · POSTGRESQL". The words are real text (they say what the product is).
 */
export function Plate({ code, children }: { code: string; children: ReactNode }) {
  return (
    <p className="inline-flex max-w-full items-stretch overflow-hidden rounded-[4px] border font-mono text-[0.6875rem] leading-none tracking-[0.08em]">
      <span className="flex items-center bg-signal px-2 py-1.5 font-semibold text-on-signal">
        {code}
      </span>
      <span className="flex min-w-0 items-center px-2.5 py-1.5 text-fg-muted">{children}</span>
    </p>
  )
}

/**
 * The strip across the top of an instrument panel: a channel name on the left (with a small light),
 * anything on the right (a button, a count). Solid muted colour, so its text is always measurable.
 */
export function ChannelBar({
  live = false,
  label,
  children,
  className,
}: {
  live?: boolean
  label: ReactNode
  children?: ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b bg-muted px-4 py-2',
        className,
      )}
    >
      <p className="label-mono flex min-w-0 items-center gap-2">
        <span
          aria-hidden="true"
          className={cn('size-1.5 shrink-0 rounded-full', live ? 'glow bg-signal' : 'bg-input')}
        />
        {label}
      </p>
      {children}
    </div>
  )
}
