import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { clarification } from '@/mocks/fixtures'

import { ClarificationCell } from './ClarificationCell'

function renderCell(busy = false) {
  const onAnswer = vi.fn<(question: string) => void>()
  render(<ClarificationCell reply={clarification} busy={busy} onAnswer={onAnswer} />)
  return { onAnswer, user: userEvent.setup() }
}

const ORIGINAL = clarification.question

describe('ClarificationCell', () => {
  it('shows the question back, with each option as a button in a named list', () => {
    renderCell()

    expect(screen.getByText(clarification.clarifying_question)).toBeInTheDocument()
    expect(
      screen.getByRole('list', { name: clarification.clarifying_question }),
    ).toBeInTheDocument()
    for (const option of clarification.options) {
      expect(screen.getByRole('button', { name: option })).toBeInTheDocument()
    }
  })

  it('a click asks the original question with that option', async () => {
    const { onAnswer, user } = renderCell()

    await user.click(screen.getByRole('button', { name: 'by total revenue' }))

    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(`${ORIGINAL} (by total revenue)`)
  })

  it('options wait while a question runs, and keep their place for the keyboard', async () => {
    const { onAnswer, user } = renderCell(true)
    const option = screen.getByRole('button', { name: 'by total revenue' })

    await user.click(option)

    expect(onAnswer).not.toHaveBeenCalled()
    expect(option).toHaveAttribute('aria-disabled', 'true')
    expect(option).toBeEnabled()
  })

  it('"Something else…" opens a labelled box with the focus in it; Enter sends', async () => {
    const { onAnswer, user } = renderCell()
    const toggle = screen.getByRole('button', { name: 'Something else…' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    await user.click(toggle)

    const box = screen.getByRole('textbox', { name: 'Your answer' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(box).toHaveFocus()

    await user.keyboard('  by number of returns  {Enter}')

    expect(onAnswer).toHaveBeenCalledExactlyOnceWith(`${ORIGINAL} (by number of returns)`)
    // It closes, and the focus goes back instead of being lost with the form.
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })

  it('Escape closes the box without sending, and the focus goes back', async () => {
    const { onAnswer, user } = renderCell()
    const toggle = screen.getByRole('button', { name: 'Something else…' })

    await user.click(toggle)
    await user.keyboard('half a tho{Escape}')

    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('an empty answer gets a message, not a request', async () => {
    const { onAnswer, user } = renderCell()

    await user.click(screen.getByRole('button', { name: 'Something else…' }))
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Type your answer first.')
    expect(screen.getByRole('textbox', { name: 'Your answer' })).toHaveAccessibleDescription(
      'Type your answer first.',
    )
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('an answer over the limit says how much to remove, and keeps the text', async () => {
    const { onAnswer, user } = renderCell()

    await user.click(screen.getByRole('button', { name: 'Something else…' }))
    const box = screen.getByRole('textbox', { name: 'Your answer' })
    await user.click(box)
    await user.paste('x'.repeat(1005))
    await user.click(screen.getByRole('button', { name: 'Send' }))

    expect(screen.getByRole('alert')).toHaveTextContent('Remove 5 to send it.')
    expect(box).toHaveValue('x'.repeat(1005))
    expect(onAnswer).not.toHaveBeenCalled()
  })

  it('does not send a typed answer while a question runs', async () => {
    const { onAnswer, user } = renderCell(true)

    await user.click(screen.getByRole('button', { name: 'Something else…' }))
    await user.keyboard('by returns{Enter}')

    expect(onAnswer).not.toHaveBeenCalled()
    expect(screen.getByRole('textbox', { name: 'Your answer' })).toHaveValue('by returns')
  })
})
