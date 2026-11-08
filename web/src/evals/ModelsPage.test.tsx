import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { evals } from '@/mocks/fixtures'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const GOLDEN_TITLE = 'Golden 58 (Northwind 30 + business 13 + Chinook 15)'
const NOTE = 'so far only golden, business'

const study = (name: string | RegExp) => screen.findByRole('region', { name })

describe('ModelsPage', () => {
  it('shows the "so far only" note when the data has an incomplete suite', async () => {
    renderApp('/evals/models')

    expect(
      await screen.findByRole('heading', { level: 2, name: `${GOLDEN_TITLE}: ${NOTE}` }),
    ).toBeVisible()
  })

  it('shows no such note when every suite is complete', async () => {
    const complete = {
      ...evals.models,
      studies: evals.models.studies.map((s) => ({ ...s, partial_note: null })),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json({ ...evals, models: complete })))
    renderApp('/evals/models')

    expect(await screen.findByRole('heading', { level: 2, name: GOLDEN_TITLE })).toBeVisible()
    expect(screen.queryByText(/so far only/)).not.toBeInTheDocument()
  })

  it.each([
    [
      'gemini / gemini-3.5-flash-lite',
      ['43/43', '1.9', '12.3', '3,486 + 145', '$0.00141', '$1.41', '$0.00141'],
    ],
    [
      'groq / qwen/qwen3.8-27b',
      ['42/43', '4.1', '29.4', '4,318 + 151', '$0.00406', '$4.06', '$0.00415'],
    ],
    [
      'groq / openai/gpt-oss-20b',
      ['41/43', '1.7', '2.5', '3,564 + 405', '$0.00039', '$0.39', '$0.00041'],
    ],
    [
      'groq / openai/gpt-oss-120b',
      ['40/43', '2.2', '3.9', '3,685 + 422', '$0.00081', '$0.81', '$0.00087'],
    ],
  ])("the table has the report's numbers for %s", async (llm, numbers) => {
    renderApp('/evals/models')

    const golden = await study(`${GOLDEN_TITLE}: every number`)
    const row = within(golden)
      .getAllByRole('row')
      .find((candidate) => candidate.textContent?.startsWith(llm))
    expect(row).toBeDefined()
    const cells = within(row as HTMLElement)
      .getAllByRole('cell')
      .map((cell) => cell.textContent)
    expect(cells).toEqual(numbers)
  })

  it('labels the chart with every model, and says what the gaps are worth', async () => {
    renderApp('/evals/models')

    const chart = await screen.findByRole('img', { name: /^Scatter chart of 4 models/ })
    for (const name of ['gemini-3.5-flash-lite', 'qwen3.8-27b', 'gpt-oss-20b', 'gpt-oss-120b']) {
      expect(chart).toHaveAccessibleName(expect.stringContaining(`${name}: `))
    }
    expect(
      screen.getByText(/The accuracy gap is 3 of 43 questions, more than the noise/),
    ).toBeVisible()
    expect(screen.getByText(/The cost gap is about 10 times/)).toBeVisible()
  })

  it('gives every time in words next to its bar', async () => {
    renderApp('/evals/models')

    const speed = await screen.findByRole('listitem', { name: 'Speed of qwen3.8-27b' })
    expect(speed).toHaveTextContent('p50 4.1 s')
    expect(speed).toHaveTextContent('p95 29.4 s')
  })

  it('names the models that have not run', async () => {
    const notRun = [{ llm: 'groq / openai/gpt-oss-120b', missing: ['golden', 'business'] }]
    const models = {
      ...evals.models,
      studies: evals.models.studies.map((s) =>
        s.name === 'golden' ? { ...s, not_run: notRun } : s,
      ),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json({ ...evals, models })))
    renderApp('/evals/models')

    expect(
      await screen.findByText('Not run yet: groq / openai/gpt-oss-120b (golden, business).'),
    ).toBeVisible()
  })

  it('says BIRD has only one model, with no chart pretending to compare', async () => {
    renderApp('/evals/models')

    await screen.findByRole('img', { name: /^Scatter chart/ })
    const bird = await study('BIRD-100 (the fixed subset, evidence on)')
    expect(bird).toHaveTextContent(
      'Only one model has run this study so far: nothing to compare yet.',
    )
    expect(within(bird).queryByRole('img')).not.toBeInTheDocument()
    const row = within(bird)
      .getAllByRole('row')
      .find((candidate) => candidate.textContent?.startsWith('gemini'))
    expect(row).toHaveTextContent('57/100')
    expect(row).toHaveTextContent('63/100')
  })

  it('says the preview model is one, the price date, and what the cost leaves out', async () => {
    renderApp('/evals/models')

    expect(await screen.findByText(/a preview model on Groq/)).toBeVisible()
    expect(screen.getByText(/list prices, checked 2026-10-25/)).toBeVisible()
    const notes = screen.getByRole('region', { name: 'What these numbers leave out' })
    expect(notes).toHaveTextContent('the user-facing summary adds one more call')
  })
})
