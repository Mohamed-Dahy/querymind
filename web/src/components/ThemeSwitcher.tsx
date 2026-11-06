import { Monitor, Moon, Sun, type LucideIcon } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useTheme, type ThemeChoice } from '@/lib/theme'
import { cn } from '@/lib/utils'

const OPTIONS: { choice: ThemeChoice; label: string; Icon: LucideIcon }[] = [
  { choice: 'dark', label: 'Dark', Icon: Moon },
  { choice: 'light', label: 'Light', Icon: Sun },
  { choice: 'system', label: 'System', Icon: Monitor },
]

type ThemeSwitcherProps = {
  /** On a phone, icons only (the words stay for screen readers). The top bar uses this. */
  compact?: boolean
}

/** Three toggle buttons; the pressed one is the current choice (aria-pressed, not colour alone). */
export function ThemeSwitcher({ compact = false }: ThemeSwitcherProps) {
  const { choice, setChoice } = useTheme()

  return (
    <div role="group" aria-label="Theme" className="inline-flex gap-1 rounded-lg bg-muted p-1">
      {OPTIONS.map(({ choice: option, label, Icon }) => (
        <Button
          key={option}
          size="sm"
          variant={choice === option ? 'outline' : 'ghost'}
          aria-pressed={choice === option}
          className={cn(compact && 'pointer-coarse:min-w-11')}
          onClick={() => setChoice(option)}
        >
          <Icon aria-hidden="true" />
          <span className={cn(compact && 'sr-only sm:not-sr-only')}>{label}</span>
        </Button>
      ))}
    </div>
  )
}
