import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { Composer } from './Composer'
import { EmptyCanvas } from './EmptyCanvas'
import { EXAMPLE_GROUPS } from './examples'

function setup(busy = false) {
  const onSubmit = vi.fn<(question: string) => void>()
  const user = userEvent.setup()
  const view = render(<Composer onSubmit={onSubmit} busy={busy} />)
  const box = screen.getByRole('textbox', { name: 'Your question' })
  return { onSubmit, user, box, view }
}

describe('Composer', () => {
  it('sends the trimmed question with the Ask button, then clears the box', async () => {
    const { onSubmit, user, box } = setup()

    await user.type(box, '  How many customers are there?  ')
    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(onSubmit).toHaveBeenCalledExactlyOnceWith('How many customers are there?')
    expect(box).toHaveValue('')
  })

  it.each(['{Control>}{Enter}{/Control}', '{Meta>}{Enter}{/Meta}'])(
    'sends with the shortcut %s',
    async (shortcut) => {
      const { onSubmit, user, box } = setup()

      await user.type(box, 'Top 5 products')
      await user.keyboard(shortcut)

      expect(onSubmit).toHaveBeenCalledExactlyOnceWith('Top 5 products')
    },
  )

  it('adds a new line on plain Enter instead of sending', async () => {
    const { onSubmit, user, box } = setup()

    await user.type(box, 'line one{Enter}line two')

    expect(onSubmit).not.toHaveBeenCalled()
    expect(box).toHaveValue('line one\nline two')
  })

  it('explains an empty question next to the box, and the message goes away on typing', async () => {
    const { onSubmit, user, box } = setup()

    await user.click(screen.getByRole('button', { name: 'Ask' }))

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Type a question first.')
    expect(box).toHaveAttribute('aria-invalid', 'true')
    expect(box).toHaveAccessibleDescription(/Type a question first\./)

    await user.type(box, 'a')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('refuses a question over 1,000 characters and says how much to remove', async () => {
    const { onSubmit, user, box } = setup()

    await user.click(box)
    await user.paste('a'.repeat(1003))
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Remove 3 to send it.')
    expect(screen.getByText('1,003 / 1,000')).toBeInTheDocument()
    // The text stays, so the user can shorten it.
    expect(box).toHaveValue('a'.repeat(1003))
  })

  it('counts characters, and announces only near the limit', async () => {
    const { user, box, view } = setup()

    await user.type(box, 'hello')
    expect(screen.getByText('5 / 1,000')).toBeInTheDocument()
    const live = view.container.querySelector('[aria-live="polite"]')
    expect(live).toHaveTextContent('')

    await user.paste('a'.repeat(950))
    expect(live).toHaveTextContent('50 characters or fewer left.')
  })

  it('waits while busy: shows the spinner label and does not send, but typing still works', async () => {
    const { onSubmit, user, box } = setup(true)

    const button = screen.getByRole('button', { name: 'Asking…' })
    expect(button).toHaveAttribute('aria-disabled', 'true')

    await user.type(box, 'next question')
    await user.click(button)
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(onSubmit).not.toHaveBeenCalled()
    expect(box).toHaveValue('next question')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})

describe('EmptyCanvas', () => {
  it('shows each group with its questions, and a click asks one', async () => {
    const onAsk = vi.fn<(question: string) => void>()
    const user = userEvent.setup()
    render(<EmptyCanvas onAsk={onAsk} />)

    for (const group of EXAMPLE_GROUPS) {
      const section = screen.getByRole('region', { name: group.title })
      expect(section).toBeInTheDocument()
    }
    await user.click(screen.getByRole('button', { name: 'How many key accounts do we have?' }))

    expect(onAsk).toHaveBeenCalledExactlyOnceWith('How many key accounts do we have?')
  })

  it('does not ask while busy', async () => {
    const onAsk = vi.fn<(question: string) => void>()
    const user = userEvent.setup()
    render(<EmptyCanvas onAsk={onAsk} busy />)

    await user.click(screen.getByRole('button', { name: 'How many customers are there?' }))

    expect(onAsk).not.toHaveBeenCalled()
  })
})
