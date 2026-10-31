import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { AskRequest } from '@/api/client'
import { answer, followup } from '@/mocks/fixtures'
import { apiError, ASK_URL, replyFor } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

/** Record every POST /api/ask body, and answer like the normal fake API. */
function recordAsks(): AskRequest[] {
  const bodies: AskRequest[] = []
  server.use(
    http.post(ASK_URL, async ({ request }) => {
      const body = (await request.json()) as AskRequest
      bodies.push(body)
      return HttpResponse.json(replyFor(body))
    }),
  )
  return bodies
}

async function askByKeyboard(user: ReturnType<typeof userEvent.setup>, question: string) {
  await user.type(screen.getByRole('textbox', { name: 'Your question' }), question)
  await user.keyboard('{Control>}{Enter}{/Control}')
}

describe('CanvasPage', () => {
  it('asks, shows the answer, and puts the new conversation in the URL', async () => {
    // Hold the reply until the test lets it go, to see the running cell first.
    let release = () => {}
    const replied = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.post(ASK_URL, async ({ request }) => {
        await replied
        return HttpResponse.json(replyFor((await request.json()) as AskRequest))
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await user.click(screen.getByRole('button', { name: 'Which products need restocking?' }))

    // At once: a running cell, and the examples are gone.
    expect(
      screen.getByRole('article', { name: 'Which products need restocking?' }),
    ).toHaveAttribute('aria-busy', 'true')
    expect(screen.queryByRole('region', { name: 'Northwind shop' })).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')

    release()
    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\/[0-9a-f-]{36}$/))
    // Replace, not push: Back doesn't return to an empty `/`.
    expect(router.state.historyAction).toBe('REPLACE')
    // Still the same conversation after the URL change: the cell stayed, the examples never came back.
    expect(
      screen.getByRole('article', { name: 'Which products need restocking?' }),
    ).toHaveAttribute('aria-busy', 'false')
    expect(screen.queryByRole('region', { name: 'Northwind shop' })).not.toBeInTheDocument()
    expect(screen.getByText('Answer ready.')).toBeInTheDocument()
  })

  it('keeps focus in the composer while the URL changes (no remount, no focus jump)', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const box = screen.getByRole('textbox', { name: 'Your question' })

    await askByKeyboard(user, 'Top 3 artists')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))

    // The very same element (CanvasPage wasn't remounted), and it still has focus.
    expect(screen.getByRole('textbox', { name: 'Your question' })).toBe(box)
    expect(box).toHaveFocus()
  })

  it('sends the conversation id with a follow-up, and adds a second cell', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await askByKeyboard(user, 'Now only for 1997')
    await screen.findByText(followup.answer)

    const id = router.state.location.pathname.slice('/c/'.length)
    expect(bodies).toEqual([
      { question: 'Sales per category?', conversation_id: null },
      { question: 'Now only for 1997', conversation_id: id },
    ])
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })

  it('keeps the conversation when you visit another page and come back', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    const conversationPath = router.state.location.pathname

    await user.click(screen.getByRole('link', { name: 'Explore' }))
    await screen.findByRole('heading', { level: 1, name: 'Data explorer' })
    await router.navigate(conversationPath)

    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
  })

  it('does not change the URL when the first question fails', async () => {
    server.use(http.post(ASK_URL, () => apiError('db_unavailable')))
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    const cell = await screen.findByRole('article', { name: 'Sales per category?' })
    expect(
      await within(cell).findByText("The database isn't reachable right now."),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')
  })

  it('shows a question back and a refusal (their full cells arrive in U1.6)', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Delete all orders from 1996')
    expect(await screen.findByText('No SQL ran.')).toBeInTheDocument()
    expect(screen.getByText('QueryMind declined this question.')).toBeInTheDocument()
  })
})
