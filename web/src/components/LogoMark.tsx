import { cn } from '@/lib/utils'

/**
 * QueryMind's mark (Signal Lab, ADR 026): a lime tile with a signal trace that rises, checks itself
 * (the dip) and lands, the agent's work in one line. Decorative: next to it the words "QueryMind"
 * give the link its name, so the SVG is hidden from screen readers.
 */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" aria-hidden="true" className={cn('size-7 shrink-0', className)}>
      <rect width="32" height="32" rx="8" className="fill-signal" />
      <path
        d="M6 21 L11 21 L14 11 L18 23 L21 15 L26 15"
        fill="none"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="stroke-on-signal"
      />
      <circle cx="26" cy="15" r="2" className="fill-on-signal" />
    </svg>
  )
}
