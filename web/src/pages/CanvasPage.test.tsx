import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { renderApp } from '@/test/render'

// Until U1.3 sends questions to the API, the page only confirms what it received.
describe('CanvasPage', () => {
  it('takes a question from an example', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await user.click(screen.getByRole('button', { name: 'Which products need restocking?' }))

    expect(screen.getByText(/Received: “Which products need restocking\?”/)).toBeInTheDocument()
  })

  it('takes a typed question from the composer', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'Top 3 artists')
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(screen.getByText(/Received: “Top 3 artists”/)).toBeInTheDocument()
  })
})
