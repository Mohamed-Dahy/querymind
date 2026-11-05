import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { evals } from '@/mocks/fixtures'
import { apiError } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const tile = (name: string) => screen.findByRole('listitem', { name })

describe('EvalsPage', () => {
  it('shows the four headline numbers with what each counted', async () => {
    renderApp('/evals')

    const golden = await tile('Golden 58')
    expect(within(golden).getByText('53/53')).toBeInTheDocument()
    expect(golden).toHaveTextContent("5 of 58 questions weren't scored")

    const bird = await tile('BIRD-100')
    expect(within(bird).getByText('57/100')).toBeInTheDocument()
    expect(bird).toHaveTextContent('60/100')

    const gate = await tile('CI gate baseline')
    expect(within(gate).getByText('12/12')).toBeInTheDocument()
    expect(gate).toHaveTextContent("hasn't been seen failing")

    const cheapest = await tile('Cheapest per correct answer')
    expect(within(cheapest).getByText('$0.00041')).toBeInTheDocument()
    expect(cheapest).toHaveTextContent('groq / openai/gpt-oss-20b')
  })

  it('names the report and date of every headline number', async () => {
    renderApp('/evals')

    const bird = await tile('BIRD-100')
    expect(bird).toHaveTextContent('phase4-bird-100-seq')
    expect(bird).toHaveTextContent('2026-10-24')
  })

  it("names BIRD's licence and links to BIRD", async () => {
    renderApp('/evals')

    const link = within(await tile('BIRD-100')).getByRole('link', { name: /CC BY-SA 4\.0/ })
    expect(link).toHaveAttribute('href', evals.bird.url)
    expect(link).toHaveAttribute('target', '_blank')
  })

  it('links each score to the questions behind it', async () => {
    renderApp('/evals')

    expect(
      within(await tile('Golden 58')).getByRole('link', { name: 'See every question' }),
    ).toHaveAttribute('href', '/evals/questions')
    expect(
      within(await tile('BIRD-100')).getByRole('link', { name: 'See every BIRD question' }),
    ).toHaveAttribute('href', '/evals/questions?study=bird')
  })

  it('links to the live gate status instead of claiming one', async () => {
    renderApp('/evals')

    const gate = await tile('CI gate baseline')
    expect(within(gate).getByRole('link', { name: /Live status on GitHub/ })).toHaveAttribute(
      'href',
      evals.gate.actions_url,
    )
    expect(gate).not.toHaveTextContent(/\bpassing\b/)
  })

  it('says how old the results are', async () => {
    renderApp('/evals')

    expect(await screen.findByText(/the newest from 2026-10-25/)).toBeInTheDocument()
  })

  it('shows every phase as its own labelled point, with no line joining them', async () => {
    renderApp('/evals')

    const section = await screen.findByRole('region', { name: 'Phase by phase' })
    expect(section).toHaveTextContent('These aren’t a trend')
    const points = within(section).getAllByRole('listitem')
    expect(points).toHaveLength(evals.milestones.length)
    evals.milestones.forEach((point, index) => {
      expect(points[index]).toHaveTextContent(`${point.passed}/${point.total}`)
      expect(points[index]).toHaveTextContent(point.question_set)
      expect(points[index]).toHaveTextContent(point.llm)
      expect(points[index]).toHaveTextContent(point.report)
    })
    expect(section.querySelector('svg')).toBeNull()
  })

  it("says Phase 4's golden point had questions left out", async () => {
    renderApp('/evals')

    const section = await screen.findByRole('region', { name: 'Phase by phase' })
    const point = within(section)
      .getAllByRole('listitem')
      .find((item) => item.textContent?.includes('53/53'))
    expect(point).toHaveTextContent('5 more not scored')
  })

  it('names the separate run behind answer quality, and warns about the judge', async () => {
    renderApp('/evals')

    const section = await screen.findByRole('region', { name: 'Answer quality' })
    expect(section).toHaveTextContent('Its own run')
    expect(section).toHaveTextContent('groq / openai/gpt-oss-120b answered')
    expect(section).toHaveTextContent('gemini / gemini-3.5-flash-lite judged')
    const row = within(section).getByRole('row', { name: /^faithfulness/ })
    expect(row).toHaveTextContent('0.93')
    expect(row).toHaveTextContent('27/29')
    expect(section).toHaveTextContent(/e03_german_customers, e04_out_of_stock: Judge noise/)
    expect(section).toHaveTextContent('1.000')
    expect(section).toHaveTextContent('58 of 58 questions')
    expect(section).toHaveTextContent('0.268')
    expect(section).toHaveTextContent('0.940')
  })

  it('shows the Ray speed-ups with what limited them', async () => {
    renderApp('/evals')

    const section = await screen.findByRole('region', { name: 'Parallel runs with Ray' })
    expect(section).toHaveTextContent('1.6×')
    expect(section).toHaveTextContent('798 s to 508 s')
    expect(section).toHaveTextContent('2.7×')
    expect(section).toHaveTextContent('rate limiter')
  })

  it('leaves the panel out when there is no quality run', async () => {
    server.use(http.get('/api/evals', () => HttpResponse.json({ ...evals, quality: null })))
    renderApp('/evals')

    await tile('Golden 58')
    expect(screen.queryByRole('region', { name: 'Answer quality' })).not.toBeInTheDocument()
  })

  it('says so when the results file is missing, and can try again', async () => {
    let failing = true
    server.use(
      http.get('/api/evals', () =>
        failing ? apiError('evals_unavailable') : HttpResponse.json(evals),
      ),
    )
    const user = userEvent.setup()
    renderApp('/evals')

    expect(await screen.findByText("The eval results aren't available.")).toBeInTheDocument()
    expect(screen.getByText(/The rest of the app still works/)).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Evals lab' })).toBeInTheDocument()

    failing = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await tile('Golden 58')).toBeInTheDocument()
  })
})
