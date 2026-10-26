import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind-merge must know our custom theme names to resolve conflicts (rounded-lg vs rounded-cell).
const twMerge = extendTailwindMerge({
  extend: { theme: { radius: ['cell'] } },
})

/**
 * Join class names, dropping falsy ones (clsx), and let later Tailwind classes win over earlier
 * conflicting ones (tailwind-merge): cn('px-2 h-8', big && 'h-11') → 'px-2 h-11'.
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs))
}
