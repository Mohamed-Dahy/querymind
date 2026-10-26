import { describe, expect, it } from 'vitest'

import { cn } from './utils'

describe('cn', () => {
  it('drops falsy values', () => {
    expect(cn('px-2', false, null, undefined, 'py-1')).toBe('px-2 py-1')
  })

  it('lets the later of two conflicting classes win', () => {
    expect(cn('h-8', 'h-11')).toBe('h-11')
    expect(cn('bg-surface', 'bg-muted')).toBe('bg-muted')
  })

  it('knows our custom radius and keeps size and colour classes apart', () => {
    expect(cn('rounded-lg', 'rounded-cell')).toBe('rounded-cell')
    expect(cn('text-sm', 'text-fg-muted')).toBe('text-sm text-fg-muted')
  })
})
