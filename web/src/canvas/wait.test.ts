import { describe, expect, it } from 'vitest'

import { formatWait } from './wait'

describe('formatWait', () => {
  it.each([
    [1, '1 second'],
    [18, '18 seconds'],
    [60, '1 minute'],
    [61, '1 minute 1 second'],
    [460, '7 minutes 40 seconds'],
    [3600, '1 hour'],
    [7500, '2 hours 5 minutes'],
    [7505, '2 hours 5 minutes'],
  ])('%i seconds is "%s"', (seconds, text) => {
    expect(formatWait(seconds)).toBe(text)
  })
})
