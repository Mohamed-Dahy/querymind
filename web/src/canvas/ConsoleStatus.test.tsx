import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { DEFAULT_LIMITS } from '@/lib/limits'

import { ConsoleStatus } from './ConsoleStatus'

/** Each reading as "TERM: value", the way a screen reader pairs a <dt> with its <dd>. */
function readings(): string[] {
  return screen
    .getAllByRole('term')
    .map((term) => `${term.textContent}: ${term.nextElementSibling?.textContent}`)
}

describe('ConsoleStatus', () => {
  it('shows the source and the default limits, saying they are defaults', () => {
    render(<ConsoleStatus source="Northwind + Chinook" />)

    expect(readings()).toEqual([
      'SOURCE: Northwind + Chinook',
      'ACCESS: Read-only',
      'LIMITS: 10 s · 1,000 rows by default',
      'REPAIRS: Up to 3 tries by default',
    ])
  })

  it("names the workspace's own database when that is the source", () => {
    render(<ConsoleStatus source="Sales warehouse" />)

    expect(readings()[0]).toBe('SOURCE: Sales warehouse')
  })

  it('matches the server defaults in config.py', () => {
    // If this fails, config.py's defaults changed: change lib/limits.ts (and these numbers) with them.
    expect(DEFAULT_LIMITS).toEqual({ timeoutSeconds: 10, maxRows: 1000, sqlAttempts: 3 })
  })
})
