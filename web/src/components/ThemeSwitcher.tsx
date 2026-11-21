import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'
import { LayoutGroup, m } from 'motion/react'
import { useId } from 'react'

import { useTheme, type ThemeChoice } from '@/lib/theme'
import { cn } from '@/lib/utils'
import { spring } from '@/motion'

const OPTIONS: { choice: ThemeChoice; label: string; Icon: LucideIcon }[] = [
  { choice: 'dark', label: 'Dark', Icon: Moon },
  { choice: 'light', label: 'Light', Icon: Sun },
  { choice: 'system', label: 'System', Icon: Monitor },
]

type ThemeSwitcherProps = {
  /** On a phone, icons only (the words stay for screen readers). The top bar uses this. */
  compact?: boolean
}

/**
 * Three toggle buttons in one segmented control; the pressed one is the current choice (aria-pressed,
 * not colour alone). Signal Lab (U8.2): a raised pill slides to the chosen button (motion's
 * `layoutId`, one LayoutGroup per switcher so two on a page never share it).
 */
export function ThemeSwitcher({ compact = false }: ThemeSwitcherProps) {
  const { choice, setChoice } = useTheme()
  const group = useId()

  return (
    <LayoutGroup id={group}>
      <div
        role="group"
        aria-label="Theme"
        className="inline-flex gap-0.5 rounded-lg border border-border bg-muted p-0.5"
      >
        {OPTIONS.map(({ choice: option, label, Icon }) => {
          const pressed = choice === option
          return (
            <button
              key={option}
              type="button"
              aria-pressed={pressed}
              className={cn(
                'relative isolate inline-flex h-7 items-center justify-center gap-1.5 rounded-md px-2.5 text-[0.8rem] font-medium transition-colors outline-none',
                'focus-visible:ring-3 focus-visible:ring-ring pointer-coarse:min-h-11 [&_svg]:size-3.5',
                pressed ? 'text-fg' : 'text-fg-muted hover:text-fg',
                compact && 'pointer-coarse:min-w-11',
              )}
              onClick={() => setChoice(option)}
            >
              {pressed && (
                <m.span
                  aria-hidden="true"
                  layoutId="theme-pill"
                  transition={spring.snappy}
                  // Behind the icon and words: -z-10 inside the button's own layer (isolate).
                  className="absolute inset-0 -z-10 rounded-md border border-input bg-surface"
                />
              )}
              <Icon aria-hidden="true" />
              <span className={cn(compact && 'sr-only sm:not-sr-only')}>{label}</span>
            </button>
          )
        })}
      </div>
    </LayoutGroup>
  )
}
