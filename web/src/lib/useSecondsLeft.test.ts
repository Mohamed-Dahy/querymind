import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useSecondsLeft } from './useSecondsLeft'

describe('useSecondsLeft', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => vi.useRealTimers())

  it('counts down to 0, a second at a time', () => {
    const until = Date.now() + 3000 // once: the caller keeps the same timestamp
    const { result } = renderHook(() => useSecondsLeft(until))
    expect(result.current).toBe(3)

    act(() => vi.advanceTimersByTime(1000))
    expect(result.current).toBe(2)

    act(() => vi.advanceTimersByTime(5000))
    expect(result.current).toBe(0)
  })

  it('nothing to wait for is 0', () => {
    expect(renderHook(() => useSecondsLeft(undefined)).result.current).toBe(0)
  })
})
