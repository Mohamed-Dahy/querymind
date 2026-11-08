import { screen, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { EvalsReply } from '@/api/evals'
import { evals } from '@/mocks/fixtures'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

import { failingQuestions, questionHref, runColumns, verdictIn } from './questionRows'

const data = evals as EvalsReply
const runRow = (name: string) => screen.findByRole('row', { name: new RegExp(`^${name}\\b`) })

describe('QuestionPage', () => {
  // The step's promise: a failure is never more than two clicks from the evals lab.
  describe('every failing question opens on a run that failed it, with why', () => {
    it.each(failingQuestions(data).map(({ id, study }) => [id, study] as const))(
      '%s',
      async (id, study) => {
        renderApp(questionHref(id))

        const heading = await screen.findByRole('heading', { level: 2, name: /: failed$/ })
        const label = heading.textContent?.replace(/: failed$/, '') ?? ''
        const column = runColumns(data, study).find((candidate) => candidate.label === label)
        expect(column && verdictIn(column, id, study)).toBe('failed')
        const outcome = column?.outcomes.get(id)?.outcome
        const reason = study === 'bird' ? outcome?.ex_reason || outcome?.reason : outcome?.reason
        expect(reason).toBeTruthy()
        expect(screen.getByRole('region', { name: /: failed$/ })).toHaveTextContent(reason ?? '')
      },
      30_000,
    )
  })

  it("shows every run's verdict, our SQL and the reference", async () => {
    renderApp(questionHref('b01_key_account_count', 'Arm A'))

    expect(await runRow('Arm A')).toHaveTextContent('Failed')
    expect(await runRow('Arm C')).toHaveTextContent('Passed')
    expect(await runRow('qwen3.8-27b')).toBeVisible()
    expect(screen.getAllByRole('code').map(({ textContent }) => textContent)).toEqual(
      expect.arrayContaining([
        expect.stringContaining('FROM customer_customer_demo'),
        expect.stringContaining('HAVING sum(d.unit_price'),
      ]),
    )
  })

  it('keeps QueryMind match and BIRD EX apart', async () => {
    renderApp(questionHref('b01_key_account_count', 'Arm A'))

    const reasons = await screen.findByRole('region', { name: 'Arm A: failed' })
    expect(reasons).toHaveTextContent(
      "QueryMind match: Failed. no column matches the values of 'count'",
    )
    expect(reasons).toHaveTextContent('BIRD EX: Failed. official EX: 1 expected rows missing')
  })

  it("gives BIRD's rule the run's own reason when there was no SQL for it to compare", async () => {
    renderApp(questionHref('bird_95'))

    const reasons = await screen.findByRole('region', { name: /: failed$/ })
    expect(reasons).toHaveTextContent('BIRD EX: Failed. asked back instead of answering')
    expect(reasons).not.toHaveTextContent('No reason saved')
  })

  it('says what a not-run and a not-scored run are', async () => {
    const chinook = data.questions.find(({ suite }) => suite === 'chinook')

    const first = renderApp(questionHref(chinook?.id ?? '', 'gpt-oss-20b'))
    expect(await screen.findByText("This run didn't include this question.")).toBeVisible()
    first.unmount()

    // Every question of today's runs was scored, so one is lost to a rate limit here.
    const reason = 'rate limit: daily quota used up'
    const lostOne: EvalsReply = {
      ...data,
      runs: data.runs.map((run) =>
        run.key === 'phase4-ablation-golden-D-chinook'
          ? {
              ...run,
              outcomes: run.outcomes.map((outcome) =>
                outcome.question_id === 'c01_track_count'
                  ? { ...outcome, scored: false, passed: false, ex: false, reason }
                  : outcome,
              ),
            }
          : run,
      ),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json(lostOne)))
    renderApp(questionHref('c01_track_count', 'Arm D'))
    const section = await screen.findByRole('region', { name: /not scored/ })
    expect(section).toHaveTextContent(reason)
  })

  it('labels quality scores with their own run, and the judge-noise note', async () => {
    renderApp(questionHref('e03_german_customers'))

    const panel = await screen.findByRole('region', { name: 'Answer quality' })
    expect(panel).toHaveTextContent('A separate run, not one of those above')
    expect(panel).toHaveTextContent('groq / openai/gpt-oss-120b answered')
    expect(panel).toHaveTextContent('faithfulness: 0.00, failed')
    expect(panel).toHaveTextContent('Judge noise.')
  })

  it('has no quality panel for a question nobody judged', async () => {
    renderApp(questionHref('b01_key_account_count'))

    await runRow('Arm A')
    expect(screen.queryByRole('region', { name: 'Answer quality' })).not.toBeInTheDocument()
  })

  it('BIRD without its text says why, and links to BIRD', async () => {
    renderApp(questionHref('bird_100'))

    expect(await screen.findByText(/aren't bundled with this app/)).toHaveTextContent(
      'CC BY-SA 4.0',
    )
    expect(screen.getByRole('link', { name: /Read them on BIRD's site/ })).toHaveAttribute(
      'href',
      'https://bird-bench.github.io',
    )
    expect(screen.getByText('Not bundled: see the note above.')).toBeVisible()
  })

  it('BIRD with its text shows it, in place of that note', async () => {
    const withText = {
      ...data,
      bird_text_available: true,
      questions: data.questions.map((question) =>
        question.id === 'bird_100'
          ? {
              ...question,
              question: 'How many accounts?\nHint: count them',
              reference_sql: 'SELECT 42 AS answer',
            }
          : question,
      ),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json(withText)))
    renderApp(questionHref('bird_100'))

    const asked = await screen.findByText(/How many accounts\?/)
    expect(asked.textContent).toBe('How many accounts?\nHint: count them')
    expect(asked).toHaveClass('whitespace-pre-line')
    expect(screen.queryByText(/aren't bundled/)).not.toBeInTheDocument()
    const code = screen.getAllByRole('code').map(({ textContent }) => textContent)
    expect(code).toEqual(expect.arrayContaining([expect.stringContaining('SELECT 42 AS answer')]))
  })

  it('an unknown id is the 404 page', async () => {
    renderApp('/evals/questions/no_such_question')

    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeVisible()
  })

  it('a run name from a mistyped link falls back to the usual run', async () => {
    renderApp(questionHref('b01_key_account_count', 'Arm Z'))

    const section = await screen.findByRole('region', { name: /: failed$/ })
    expect(within(section).getByText(/QueryMind match:/)).toBeVisible()
  })
})
