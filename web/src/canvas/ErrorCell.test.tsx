import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { errorTitle } from '@/api/errors'
import { errors, type MockErrorCode } from '@/mocks/fixtures'

import { ErrorCell } from './ErrorCell'
import type { CellError } from './state'

function renderCell(error: CellError, busy = false) {
  const onRetry = vi.fn<() => void>()
  const onRephrase = vi.fn<() => void>()
  const view = render(
    <ErrorCell error={error} busy={busy} onRetry={onRetry} onRephrase={onRephrase} />,
  )
  return { onRetry, onRephrase, ...view }
}

const RETRY = { name: 'Retry' }
const REPHRASE = { name: 'Rephrase' }

describe('ErrorCell', () => {
  const codes = Object.keys(errors) as MockErrorCode[]

  it.each(codes)('%s shows its own title and the server message', (code) => {
    const { message } = errors[code]
    renderCell({ code, message })

    expect(errorTitle(code)).not.toBe('Something went wrong.')
    expect(screen.getByText(errorTitle(code))).toBeInTheDocument()
    expect(screen.getByText(message)).toBeInTheDocument()
  })

  it.each(['query_failed', 'llm_unavailable', 'api_unreachable'])(
    '%s offers Retry, which can be pressed at once',
    (code) => {
      const { onRetry } = renderCell({ code, message: 'x' })

      fireEvent.click(screen.getByRole('button', RETRY))

      expect(onRetry).toHaveBeenCalledOnce()
      expect(screen.queryByRole('button', REPHRASE)).not.toBeInTheDocument()
    },
  )

  it.each(['unsafe_sql', 'invalid_request'])(
    '%s has no Retry (it would fail the same way), but Rephrase',
    (code) => {
      const { onRephrase } = renderCell({ code, message: 'x' })

      expect(screen.queryByRole('button', RETRY)).not.toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', REPHRASE))
      expect(onRephrase).toHaveBeenCalledOnce()
    },
  )

  it('Retry waits while another question runs', () => {
    const { onRetry } = renderCell({ code: 'query_failed', message: 'x' }, true)
    const retry = screen.getByRole('button', RETRY)

    fireEvent.click(retry)

    expect(onRetry).not.toHaveBeenCalled()
    expect(retry).toHaveAttribute('aria-disabled', 'true')
    expect(retry).toBeEnabled()
  })

  it('a rate limit without a known wait can be retried at once, with no countdown', () => {
    const { onRetry } = renderCell({ code: 'llm_rate_limited', message: 'x' })

    expect(screen.queryByText(/Try again in/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', RETRY))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})

describe('ErrorCell countdown', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  function advance(seconds: number) {
    act(() => {
      vi.advanceTimersByTime(seconds * 1000)
    })
  }

  function rateLimit(seconds: number): CellError {
    return {
      code: 'llm_rate_limited',
      message: 'x',
      retryAfterSeconds: seconds,
      retryAt: Date.now() + seconds * 1000,
    }
  }

  it('counts down, keeps Retry waiting, then enables it and says so', () => {
    const { onRetry } = renderCell(rateLimit(3))
    const retry = screen.getByRole('button', RETRY)

    expect(screen.getByText('Try again in 3 seconds')).toBeInTheDocument()
    expect(retry).toHaveAttribute('aria-disabled', 'true')
    fireEvent.click(retry)
    expect(onRetry).not.toHaveBeenCalled()
    // Nothing is spoken while it counts: only the end is announced here.
    expect(screen.queryByText('You can try again now.')).not.toBeInTheDocument()

    advance(1)
    expect(screen.getByText('Try again in 2 seconds')).toBeInTheDocument()

    advance(2)
    expect(screen.queryByText(/Try again in/)).not.toBeInTheDocument()
    expect(retry).toHaveAttribute('aria-disabled', 'false')
    expect(screen.getByText('You can try again now.')).toBeInTheDocument()
    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('stops the timer at zero', () => {
    renderCell(rateLimit(1))
    expect(vi.getTimerCount()).toBe(1)

    advance(1)

    expect(vi.getTimerCount()).toBe(0)
  })

  it('leaving the page and coming back does not restart the wait', () => {
    const error = rateLimit(20)
    const { unmount } = renderCell(error)

    advance(5)
    unmount()
    advance(5)
    renderCell(error)

    expect(screen.getByText('Try again in 10 seconds')).toBeInTheDocument()
  })
})
