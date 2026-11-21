import { cn } from '@/lib/utils'

/**
 * A placeholder in the shape of what is loading. Signal Lab (ADR 026): a band of light sweeps across
 * it (transform only) instead of the whole block pulsing. The keyframe moves `transform` from -100 %
 * to 100 %; no Tailwind translate-* class here, because v4's `translate` property would add to it.
 * Under reduced motion the band is gone and the block is still. `data-slot="skeleton"` is what the tests find loading states by.
 */
function Skeleton({ className, ...props }: React.ComponentProps<'div'>) {
  return (
    <div
      data-slot="skeleton"
      className={cn(
        'relative overflow-hidden rounded-md bg-muted',
        'after:absolute after:inset-0 after:animate-sweep after:bg-linear-to-r after:from-transparent after:via-[color-mix(in_srgb,var(--fg)_7%,transparent)] after:to-transparent motion-reduce:after:hidden',
        className,
      )}
      {...props}
    />
  )
}

export { Skeleton }
