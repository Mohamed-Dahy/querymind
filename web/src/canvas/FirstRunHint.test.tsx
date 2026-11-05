import { screen, waitFor } from '@testing-library/react'
import { http } from 'msw'
import { describe, expect, it } from 'vitest'

import { answer } from '@/mocks/fixtures'
import { recordTurn } from '@/mocks/conversations'
import { apiError } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const HINT = /New here\? Pick a question below/

describe('FirstRunHint', () => {
  it('greets a first visit above the example questions', async () => {
    renderApp('/')
    expect(await screen.findByText(HINT)).toBeInTheDocument()
  })

  it('stays away once a conversation has been saved', async () => {
    recordTurn({ ...answer, conversation_id: crypto.randomUUID() }, [])
    renderApp('/')

    // The history has loaded (its conversation is listed), and there is still no hint.
    expect(await screen.findByRole('link', { name: answer.question })).toBeInTheDocument()
    expect(screen.queryByText(HINT)).not.toBeInTheDocument()
  })

  it("shows when history is off, since nobody can tell it isn't a first visit", async () => {
    server.use(http.get('/api/conversations', () => apiError('history_unavailable')))
    renderApp('/')
    await waitFor(() => expect(screen.getByText(HINT)).toBeInTheDocument())
  })
})
