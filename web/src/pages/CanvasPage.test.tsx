import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { AskRequest } from '@/api/client'
import {
  answer,
  answerStream,
  clarification,
  followup,
  refusal,
  repairStream,
} from '@/mocks/fixtures'
import {
  apiError,
  ASK_URL,
  errorStream,
  replyFor,
  replyStream,
  SAVED_QUESTIONS_URL,
  STREAM_URL,
  streamOf,
} from '@/mocks/handlers'
import { addSavedQuestion, recordTurn } from '@/mocks/conversations'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

/** Record every POST /api/ask body, and answer like the normal fake API. */
function recordAsks(): AskRequest[] {
  const bodies: AskRequest[] = []
  server.use(
    http.post(STREAM_URL, async ({ request }) => {
      const body = (await request.json()) as AskRequest
      bodies.push(body)
      return replyStream(body)
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
      http.post(STREAM_URL, async ({ request }) => {
        await replied
        return replyStream((await request.json()) as AskRequest)
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
    server.use(http.post(STREAM_URL, () => errorStream('db_unavailable')))
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    const cell = await screen.findByRole('article', { name: 'Sales per category?' })
    expect(
      await within(cell).findByText("The database isn't reachable right now."),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')
  })

  it('Retry runs the question again in the same cell, and the answer replaces the error', async () => {
    const bodies: AskRequest[] = []
    let failing = true
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        const body = (await request.json()) as AskRequest
        bodies.push(body)
        return failing ? errorStream('query_failed') : replyStream(body)
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    const retry = await screen.findByRole('button', { name: 'Retry' })
    expect(router.state.location.pathname).toBe('/')

    failing = false
    await user.click(retry)

    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
    expect(bodies).toEqual([
      { question: 'Sales per category?', conversation_id: null },
      { question: 'Sales per category?', conversation_id: null },
    ])
    // The first success names the conversation; the focus didn't vanish with the Retry button.
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveFocus()
  })

  it('a rate limit counts down from what the API said, then Retry works', async () => {
    server.use(
      http.post(STREAM_URL, () =>
        streamOf([
          {
            type: 'error',
            error: { error: 'llm_rate_limited', message: 'Limit reached.', retry_after_seconds: 1 },
          },
        ]),
      ),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    const retry = await screen.findByRole('button', { name: 'Retry' })
    expect(retry).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText('Try again in 1 second')).toBeInTheDocument()
    expect(
      screen.getByText('The AI model has reached its usage limit. You can try again in 1 second.'),
    ).toBeInTheDocument()
    expect(await screen.findByText('You can try again now.')).toBeInTheDocument()
    expect(retry).toHaveAttribute('aria-disabled', 'false')
  })

  it('unsafe SQL has no Retry: Rephrase puts the question back in the composer', async () => {
    server.use(http.post(STREAM_URL, () => errorStream('unsafe_sql')))
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await user.click(await screen.findByRole('button', { name: 'Rephrase' }))

    const box = screen.getByRole('textbox', { name: 'Your question' })
    expect(box).toHaveValue('Sales per category?')
    expect(box).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  it('shows the steps while it works, and keeps them with the answer', async () => {
    let release = () => {}
    const replied = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        const body = (await request.json()) as AskRequest
        const encoder = new TextEncoder()
        const line = (event: unknown) => encoder.encode(`data: ${JSON.stringify(event)}\n\n`)
        return new HttpResponse(
          new ReadableStream<Uint8Array>({
            async start(controller) {
              for (const event of answerStream.slice(0, 5)) controller.enqueue(line(event))
              await replied
              for (const event of answerStream.slice(5)) controller.enqueue(line(event))
              controller.enqueue(line({ type: 'final', reply: replyFor(body) }))
              controller.close()
            },
          }),
          { headers: { 'Content-Type': 'text/event-stream' } },
        )
      }),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    const cell = screen.getByRole('article', { name: 'Sales per category?' })
    const steps = within(cell).getByRole('list', { name: 'Agent steps' })
    await waitFor(() =>
      expect(within(steps).getByText('Write SQL').closest('li')).toHaveAttribute(
        'aria-current',
        'step',
      ),
    )
    expect(screen.getByText('Working on your question.')).toBeInTheDocument()

    release()
    expect(await within(cell).findByText(answer.answer)).toBeInTheDocument()
    expect(within(steps).getByText('Summarize').closest('li')).not.toHaveTextContent('waiting')
    expect(within(cell).queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
  })

  it('a repair is announced while it happens, and the rail shows it', async () => {
    const repairing = repairStream.findIndex(
      (event) => event.type === 'step' && event.node === 'repair_sql',
    )
    server.use(
      http.post(STREAM_URL, () => streamOf(repairStream.slice(0, repairing + 1), { stuck: true })),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'How many customers are there in each country?')

    expect(await screen.findByText('SQL try 1 failed, repairing.')).toBeInTheDocument()
    expect(screen.getByText(/^Try 1 failed: /)).toBeInTheDocument()
    expect(screen.getByText('2 tries', { exact: false })).toBeInTheDocument()
  })

  it('Stop ends the question: an error cell with Retry, and the focus in the composer', async () => {
    server.use(http.post(STREAM_URL, () => streamOf(answerStream.slice(0, 5), { stuck: true })))
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    const cell = screen.getByRole('article', { name: 'Sales per category?' })
    await user.click(await within(cell).findByRole('button', { name: 'Stop' }))

    expect(await within(cell).findByText('You stopped this question.')).toBeInTheDocument()
    expect(within(cell).getByText(/may still finish on the server/)).toBeInTheDocument()
    expect(within(cell).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(within(cell).queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    expect(within(cell).getByText('Write SQL').closest('li')).toHaveTextContent('stopped')
    expect(screen.getByRole('textbox', { name: 'Your question' })).toHaveFocus()
    expect(router.state.location.pathname).toBe('/')
    // Not busy any more: a new question can go at once.
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-disabled', 'false')
  })

  it('Retry after Stop runs again with fresh steps, and answers', async () => {
    let stuck = true
    server.use(
      http.post(STREAM_URL, async ({ request }) =>
        stuck
          ? streamOf(answerStream.slice(0, 5), { stuck: true })
          : replyStream((await request.json()) as AskRequest),
      ),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await user.click(await screen.findByRole('button', { name: 'Stop' }))
    stuck = false
    await user.click(await screen.findByRole('button', { name: 'Retry' }))

    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(screen.getByText('Write SQL').closest('li')).not.toHaveTextContent('stopped')
    expect(screen.getAllByRole('article')).toHaveLength(1)
  })

  it('an answer from the fallback has no rail: there were no steps to show', async () => {
    server.use(http.post(STREAM_URL, () => HttpResponse.error()))
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    await screen.findByText(answer.answer)
    expect(screen.queryByRole('list', { name: 'Agent steps' })).not.toBeInTheDocument()
  })

  it('a stream that breaks before any event falls back to POST /ask, and answers', async () => {
    let plain = 0
    server.use(
      http.post(STREAM_URL, () => HttpResponse.error()),
      http.post(ASK_URL, async ({ request }) => {
        plain += 1
        return HttpResponse.json(replyFor((await request.json()) as AskRequest))
      }),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(plain).toBe(1)
  })

  it('a stream cut off after it started is an error cell with Retry, and no second question', async () => {
    let plain = 0
    server.use(
      http.post(STREAM_URL, () => streamOf(answerStream.slice(0, 3))),
      http.post(ASK_URL, () => {
        plain += 1
        return HttpResponse.json(answer)
      }),
    )
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')

    const cell = await screen.findByRole('article', { name: 'Sales per category?' })
    expect(await within(cell).findByText('The connection to the API was lost.')).toBeInTheDocument()
    expect(within(cell).getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(plain).toBe(0)
  })

  it('Follow up links the next question to that cell, in the same conversation', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await user.click(await screen.findByRole('button', { name: 'Follow up' }))

    const box = screen.getByRole('textbox', { name: 'Your question' })
    expect(box).toHaveFocus()
    expect(box).toHaveAccessibleDescription(/^Following up on: Sales per category\?/)

    await user.keyboard('Now only for 1997{Control>}{Enter}{/Control}')
    await screen.findByText(followup.answer)

    expect(bodies[1]?.conversation_id).toBe(router.state.location.pathname.slice('/c/'.length))
    // The new cell names its parent; the note above the composer is gone.
    const cell = screen.getByRole('article', { name: 'Now only for 1997' })
    expect(cell).toHaveAccessibleDescription('Sales per category?')
    expect(within(cell).getByText('Following up on: Sales per category?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel follow-up' })).not.toBeInTheDocument()
    // The first cell follows up on nothing.
    expect(screen.getByRole('article', { name: 'Sales per category?' })).not.toHaveAttribute(
      'aria-describedby',
    )
  })

  it('Cancel follow-up: the next question is a plain one', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await user.click(await screen.findByRole('button', { name: 'Follow up' }))
    await user.click(screen.getByRole('button', { name: 'Cancel follow-up' }))

    const box = screen.getByRole('textbox', { name: 'Your question' })
    expect(box).toHaveFocus()
    expect(box).not.toHaveAccessibleDescription(/Following up/)

    await askByKeyboard(user, 'Now only for 1997')
    const cell = await screen.findByRole('article', { name: 'Now only for 1997' })
    expect(cell).not.toHaveAttribute('aria-describedby')
  })

  it('New conversation gives an empty canvas and a new id; Back returns to the old one', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    const oldPath = router.state.location.pathname

    await user.click(screen.getByRole('button', { name: 'New conversation' }))

    expect(router.state.location.pathname).toBe('/')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Northwind shop' })).toBeInTheDocument()

    await askByKeyboard(user, 'Top 3 artists')
    await screen.findByRole('article', { name: 'Top 3 artists' })
    expect(bodies[1]).toEqual({ question: 'Top 3 artists', conversation_id: null })

    await act(() => router.navigate(oldPath))
    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Top 3 artists' })).not.toBeInTheDocument()
  })

  it('New conversation cancels a first question still running, and stays at `/`', async () => {
    const signals: AbortSignal[] = []
    let release = () => {}
    const replied = new Promise<void>((resolve) => (release = resolve))
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        signals.push(request.signal)
        await replied
        return replyStream((await request.json()) as AskRequest)
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await waitFor(() => expect(signals).toHaveLength(1))
    await user.click(screen.getByRole('button', { name: 'New conversation' }))

    expect(signals[0]?.aborted).toBe(true)
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
    // Not busy any more: a new question can go at once.
    expect(screen.getByRole('button', { name: 'Ask' })).toHaveAttribute('aria-disabled', 'false')

    release()
    await act(() => new Promise((resolve) => setTimeout(resolve, 50)))
    expect(router.state.location.pathname).toBe('/')
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('New conversation leaves a question running in a named conversation to finish', async () => {
    let release = () => {}
    const replied = new Promise<void>((resolve) => (release = resolve))
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Sales per category?')
    await screen.findByText(answer.answer)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    const oldPath = router.state.location.pathname
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        await replied
        return replyStream((await request.json()) as AskRequest)
      }),
    )
    await askByKeyboard(user, 'Now only for 1997')
    await user.click(screen.getByRole('button', { name: 'New conversation' }))
    expect(router.state.location.pathname).toBe('/')

    release()
    await act(() => router.navigate(oldPath))

    expect(await screen.findByText(followup.answer)).toBeInTheDocument()
  })

  it('a saved conversation opens after a reload, with its rail, and re-runs for rows', async () => {
    const id = crypto.randomUUID()
    recordTurn({ ...answer, conversation_id: id }, answerStream)
    const user = userEvent.setup()
    renderApp(`/c/${id}`)

    const cell = await screen.findByRole('article', { name: answer.question })
    expect(within(cell).getByText(answer.answer)).toBeInTheDocument()
    expect(within(cell).getByRole('list', { name: 'Agent steps' })).toBeInTheDocument()
    expect(within(cell).getByRole('tab', { name: 'Table' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(within(cell).queryByRole('tab', { name: 'Chart' })).not.toBeInTheDocument()

    await user.click(within(cell).getByRole('button', { name: 'Re-run for rows' }))

    expect(await within(cell).findByText(/^Fresh rows from /)).toBeInTheDocument()
    expect(await within(cell).findByRole('tab', { name: 'Chart' })).toHaveAttribute(
      'aria-selected',
      'true',
    )
    expect(await within(cell).findByRole('img', { name: /^Bar chart/ })).toBeInTheDocument()
  })

  it('a follow-up on a reopened conversation continues it', async () => {
    const id = crypto.randomUUID()
    recordTurn({ ...answer, conversation_id: id }, answerStream)
    const bodies = recordAsks()
    const user = userEvent.setup()
    renderApp(`/c/${id}`)

    await screen.findByRole('article', { name: answer.question })
    await askByKeyboard(user, 'Now only for 1997')
    await screen.findByText(followup.answer)

    expect(bodies).toEqual([{ question: 'Now only for 1997', conversation_id: id }])
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })

  it('opening a saved conversation announces nothing: nothing just finished', async () => {
    const id = crypto.randomUUID()
    recordTurn({ ...answer, conversation_id: id }, answerStream)
    renderApp(`/c/${id}`)

    await screen.findByRole('article', { name: answer.question })

    expect(screen.queryByText('Answer ready.')).not.toBeInTheDocument()
  })

  it('a conversation that is gone says so, and asking there sends its id', async () => {
    const bodies = recordAsks()
    const id = '3f2a9c1e-0000-4000-8000-000000000000'
    const user = userEvent.setup()
    renderApp(`/c/${id}`)

    const notice = await screen.findByText(/This conversation wasn't found/)
    expect(notice).toBeInTheDocument()

    await askByKeyboard(user, 'Now only for 1997')
    await screen.findByText(followup.answer)

    expect(bodies).toEqual([{ question: 'Now only for 1997', conversation_id: id }])
    expect(screen.queryByText(/This conversation wasn't found/)).not.toBeInTheDocument()
  })

  it('with history off, a conversation link says so calmly, and asking still works', async () => {
    server.use(http.get('/api/conversations/:id', () => apiError('history_unavailable')))
    const user = userEvent.setup()
    renderApp('/c/3f2a9c1e-0000-4000-8000-000000000000')

    expect(await screen.findByText(/earlier questions can't be shown/)).toBeInTheDocument()

    await askByKeyboard(user, 'Now only for 1997')
    expect(await screen.findByText(followup.answer)).toBeInTheDocument()
  })

  it('a path that cannot be a conversation id is not found, not a canvas that will fail', () => {
    renderApp('/c/3f2a9c1e')

    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Your question' })).not.toBeInTheDocument()
  })

  it('a click on an option asks again in the same conversation, in a new cell', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Which products are the most popular?')
    const button = await screen.findByRole('button', { name: 'by total revenue' })
    expect(screen.getByText('QueryMind has a question for you.')).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await user.click(button)

    expect(await screen.findByText(followup.answer)).toBeInTheDocument()
    const id = router.state.location.pathname.slice('/c/'.length)
    expect(bodies).toEqual([
      { question: 'Which products are the most popular?', conversation_id: null },
      {
        question: 'Which products are the most popular? (by total revenue)',
        conversation_id: id,
      },
    ])
    // The question back stays on the canvas, above its answer.
    expect(screen.getAllByRole('article')).toHaveLength(2)
    expect(screen.getByText(clarification.clarifying_question)).toBeInTheDocument()
  })

  it('an option picked before the URL changes still continues that conversation', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Which products are the most popular?')
    await user.click(await screen.findByRole('button', { name: 'by total revenue' }))

    await screen.findByText(followup.answer)
    expect(bodies[1]?.conversation_id).toEqual(expect.any(String))
  })

  it('"Something else…" sends the typed answer in the same conversation', async () => {
    const bodies = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await askByKeyboard(user, 'Which products are the most popular?')
    await user.click(await screen.findByRole('button', { name: 'Something else…' }))
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await user.type(screen.getByRole('textbox', { name: 'Your answer' }), 'by returns{Enter}')

    await screen.findByText(followup.answer)
    expect(bodies[1]).toEqual({
      question: 'Which products are the most popular? (by returns)',
      conversation_id: router.state.location.pathname.slice('/c/'.length),
    })
  })

  it('a refusal shows its message calmly, with nothing to run or look at', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await askByKeyboard(user, 'Delete all orders from 1996')

    const cell = await screen.findByRole('article', { name: 'Delete all orders from 1996' })
    expect(await within(cell).findByText(refusal.message)).toBeInTheDocument()
    expect(within(cell).getByText('Nothing in the database was changed.')).toBeInTheDocument()
    expect(within(cell).queryByRole('tab')).not.toBeInTheDocument()
    expect(screen.getByText('QueryMind declined this question.')).toBeInTheDocument()
  })

  describe('saving a question', () => {
    async function askedOnce(user: ReturnType<typeof userEvent.setup>, question: string) {
      await askByKeyboard(user, question)
      await screen.findByText(answer.answer)
    }

    it('saves and unsaves, showing which it is', async () => {
      const user = userEvent.setup()
      renderApp('/')
      await askedOnce(user, 'Sales per category?')

      const save = await screen.findByRole('button', { name: 'Save question' })
      expect(save).toHaveAttribute('aria-pressed', 'false')
      await user.click(save)

      const saved = await screen.findByRole('button', { name: 'Saved' })
      expect(saved).toHaveAttribute('aria-pressed', 'true')
      expect(saved).toHaveFocus() // the same button: the keyboard stays where it was
      expect(
        await screen.findByRole('button', { name: 'Ask “Sales per category?”' }),
      ).toBeInTheDocument()

      await user.click(saved)

      expect(await screen.findByRole('button', { name: 'Save question' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
      expect(screen.queryByRole('button', { name: 'Ask “Sales per category?”' })).toBeNull()
    })

    it('knows a question saved earlier', async () => {
      addSavedQuestion('Sales per category?')
      const user = userEvent.setup()
      renderApp('/')
      await askedOnce(user, 'Sales per category?')

      expect(await screen.findByRole('button', { name: 'Saved' })).toHaveAttribute(
        'aria-pressed',
        'true',
      )
    })

    it('a later question is saved in the words the agent rewrote it to', async () => {
      const bodies: { question: string }[] = []
      server.use(
        http.post(SAVED_QUESTIONS_URL, async ({ request }) => {
          const body = (await request.json()) as { question: string }
          bodies.push(body)
          return HttpResponse.json(addSavedQuestion(body.question))
        }),
      )
      const id = crypto.randomUUID()
      recordTurn({ ...answer, conversation_id: id }, answerStream)
      recordTurn(
        {
          ...answer,
          question: 'Now only for 1997',
          search_question: 'Sales per category in 1997 ', // the API trims what it saves
          conversation_id: id,
        },
        answerStream,
      )
      const user = userEvent.setup()
      renderApp(`/c/${id}`)

      const later = await screen.findByRole('article', { name: 'Now only for 1997' })
      await user.click(await within(later).findByRole('button', { name: 'Save question' }))

      await within(later).findByRole('button', { name: 'Saved' })
      expect(bodies).toEqual([{ question: 'Sales per category in 1997' }])
    })

    it('a later question that was not rewritten has no button: alone, its words mean nothing', async () => {
      const id = crypto.randomUUID()
      recordTurn({ ...answer, conversation_id: id }, answerStream)
      recordTurn({ ...answer, question: 'Now only for 1997', conversation_id: id }, answerStream)
      renderApp(`/c/${id}`)

      const first = await screen.findByRole('article', { name: answer.question })
      await within(first).findByRole('button', { name: 'Save question' })
      const later = screen.getByRole('article', { name: 'Now only for 1997' })
      await within(later).findByRole('button', { name: 'Follow up' })
      expect(within(later).queryByRole('button', { name: /^Save/ })).toBeNull()
    })

    it('a failed save says so, and stays unsaved', async () => {
      server.use(http.post(SAVED_QUESTIONS_URL, () => apiError('history_unavailable')))
      const user = userEvent.setup()
      renderApp('/')
      await askedOnce(user, 'Sales per category?')

      await user.click(await screen.findByRole('button', { name: 'Save question' }))

      expect(await screen.findByText("History isn't available.")).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Save question' })).toHaveAttribute(
        'aria-pressed',
        'false',
      )
    })

    it('with history off there is no button, and the answer is still there', async () => {
      server.use(http.get(SAVED_QUESTIONS_URL, () => apiError('history_unavailable')))
      const user = userEvent.setup()
      renderApp('/')
      await askedOnce(user, 'Sales per category?')

      await screen.findByRole('button', { name: 'Follow up' })
      await waitFor(() =>
        expect(screen.queryByRole('button', { name: 'Save question' })).toBeNull(),
      )
    })

    it('asking a saved question from a conversation starts a new one, not a follow-up', async () => {
      addSavedQuestion('Top 3 artists')
      const bodies = recordAsks()
      const user = userEvent.setup()
      const { router } = renderApp('/')
      await askedOnce(user, 'Sales per category?')
      await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
      const first = router.state.location.pathname

      await user.click(await screen.findByRole('button', { name: 'Ask “Top 3 artists”' }))

      expect(await screen.findByRole('article', { name: 'Top 3 artists' })).toBeInTheDocument()
      expect(screen.queryByRole('article', { name: 'Sales per category?' })).toBeNull()
      await waitFor(() => expect(bodies).toHaveLength(2))
      expect(bodies[1]).toEqual({ question: 'Top 3 artists', conversation_id: null })
      await waitFor(() => {
        expect(router.state.location.pathname).toMatch(/^\/c\//)
        expect(router.state.location.pathname).not.toBe(first)
      })
    })

    it('asking a saved question right after the first answer, still at `/`, is new too', async () => {
      // The first reply promotes `new` to that conversation. Before the page has seen a reset, that
      // promotion would still send the saved question into the old conversation.
      addSavedQuestion('Top 3 artists')
      const bodies = recordAsks()
      const user = userEvent.setup()
      renderApp('/')
      await user.click(await screen.findByRole('button', { name: 'Ask “Top 3 artists”' }))
      await screen.findByRole('article', { name: 'Top 3 artists' })
      await waitFor(() => expect(bodies).toHaveLength(1))
      await screen.findByText(answer.answer)

      await user.click(screen.getByRole('button', { name: 'Ask “Top 3 artists”' }))

      await waitFor(() => expect(bodies).toHaveLength(2))
      expect(bodies[1]?.conversation_id).toBeNull()
    })
  })
})
