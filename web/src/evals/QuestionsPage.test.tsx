import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { EvalsReply } from '@/api/evals'
import { evals } from '@/mocks/fixtures'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

import { failingQuestions, filterOptions, questionHref, questionsOf } from './questionRows'

const data = evals as EvalsReply
const table = () => screen.findByRole('region', { name: 'Questions against runs' })
const bodyRows = async () =>
  within(await table())
    .getAllByRole('row')
    .slice(1)

describe('QuestionsPage', () => {
  it.each([
    ['golden', '/evals/questions', 58, 8],
    ['bird', '/evals/questions?study=bird', 100, 5],
  ] as const)('%s: one row per question and one column per run', async (_, path, rows, headers) => {
    renderApp(path)

    expect(await bodyRows()).toHaveLength(rows)
    expect(within(await table()).getAllByRole('columnheader')).toHaveLength(headers)
    expect(screen.getByText(`Showing ${rows} of ${rows} questions.`)).toBeVisible()
  })

  it('shows each run with the score the other views give it', async () => {
    renderApp('/evals/questions')

    const headers = within(await table()).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Question',
      'Arm A45/58',
      'Arm B45/58',
      'Arm C57/58',
      'Arm D58/58',
      'gpt-oss-120b40/43',
      'gpt-oss-20b41/43',
      'qwen3.8-27b42/43',
    ])
  })

  it('gives all four states their own shape and their own words, with no colour needed', async () => {
    // Every question of today's runs was scored, so one is lost to a rate limit here.
    const lostOne: EvalsReply = {
      ...data,
      runs: data.runs.map((run) =>
        run.key === 'phase4-ablation-golden-D-chinook'
          ? {
              ...run,
              outcomes: run.outcomes.map((outcome) =>
                outcome.question_id === 'c01_track_count'
                  ? { ...outcome, scored: false, passed: false, ex: false, reason: 'rate limit' }
                  : outcome,
              ),
            }
          : run,
      ),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json(lostOne)))
    renderApp('/evals/questions')

    const box = await table()
    for (const [verdict, word] of [
      ['passed', 'Passed'],
      ['failed', 'Failed'],
      ['not-scored', 'Not scored'],
      ['not-run', 'Not run'],
    ]) {
      expect(box.querySelector(`svg[data-verdict="${verdict}"]`)).not.toBeNull()
      expect(within(box).getAllByText(new RegExp(`: ${word}$`)).length).toBeGreaterThan(0)
    }
    const shapes = ['passed', 'failed', 'not-scored', 'not-run'].map(
      (verdict) => box.querySelector(`svg[data-verdict="${verdict}"]`)?.getAttribute('class') ?? '',
    )
    const icons = shapes.map((names) => names.match(/lucide-[a-z-]+/)?.[0])
    expect(new Set(icons).size).toBe(4)
  })

  it.each(['golden', 'bird'] as const)(
    '%s: every failing question has a link in the failures view',
    async (study) => {
      renderApp(
        study === 'bird' ? '/evals/questions?study=bird&failures=1' : '/evals/questions?failures=1',
      )

      const box = await table()
      const failing = failingQuestions(data).filter((question) => question.study === study)
      expect(failing.length).toBeGreaterThan(0)
      expect(within(box).getAllByRole('row').slice(1)).toHaveLength(failing.length)
      const hrefs = new Set([...box.querySelectorAll('a')].map((link) => link.getAttribute('href')))
      for (const { id } of failing) expect(hrefs.has(questionHref(id))).toBe(true)
    },
  )

  // The step's promise, counted the way a person would: from the page they land on.
  it('puts every failing question, in both studies, two clicks from the evals lab', async () => {
    const evalsLinks = () =>
      [...document.querySelectorAll('a')]
        .map((link) => link.getAttribute('href') ?? '')
        .filter((href) => href.startsWith('/evals'))

    const lab = renderApp('/evals')
    await screen.findByRole('listitem', { name: 'BIRD-100' })
    const firstClicks = new Set(evalsLinks())
    lab.unmount()

    const reached = new Set<string>()
    for (const href of firstClicks) {
      const view = renderApp(href)
      await screen.findByRole('navigation', { name: 'Evals lab views' })
      await waitFor(() => expect(document.querySelector('[data-slot="skeleton"]')).toBeNull())
      for (const link of evalsLinks()) reached.add(link)
      view.unmount()
    }

    const failing = failingQuestions(data)
    expect(failing.map(({ study }) => study)).toEqual(expect.arrayContaining(['golden', 'bird']))
    expect(failing.filter(({ id }) => !reached.has(questionHref(id)))).toEqual([])
  }, 30_000)

  it("names a question by its first line, leaving BIRD's hint to the question's page", async () => {
    const withHint = {
      ...data,
      questions: data.questions.map((question) =>
        question.id === 'bird_100'
          ? { ...question, question: 'How many accounts?\nHint: count them' }
          : question,
      ),
    }
    server.use(http.get('/api/evals', () => HttpResponse.json(withHint)))
    renderApp('/evals/questions?study=bird')

    const box = await table()
    expect(within(box).getByRole('link', { name: 'How many accounts?' })).toBeVisible()
    expect(box).not.toHaveTextContent('count them')
  })

  it('has one Tab stop per row: the question, not every cell', async () => {
    renderApp('/evals/questions')

    const box = await table()
    const links = [...box.querySelectorAll('a')]
    expect(links.filter((link) => link.tabIndex === 0)).toHaveLength(58)
    expect(links.filter((link) => link.tabIndex === -1)).toHaveLength(58 * 7)
  })

  it('opens the question at the run whose cell was clicked', async () => {
    renderApp('/evals/questions')

    const box = await table()
    const cell = box.querySelector(
      `a[href="${questionHref('b01_key_account_count', 'Arm B')}"]`,
    ) as HTMLElement
    await userEvent.click(cell)

    expect(
      await screen.findByRole('heading', { name: 'Question b01_key_account_count' }),
    ).toBeVisible()
    expect(await screen.findByRole('heading', { level: 2, name: /^Arm B:/ })).toBeVisible()
  })

  it('keeps the filters in the link, and narrows by them', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/evals/questions')

    await table()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Suite' }), 'chinook')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Tier' }), 'hard')

    expect(router.state.location.search).toBe('?suite=chinook&tier=hard')
    expect(await bodyRows()).toHaveLength(4)

    await user.click(screen.getByRole('checkbox', { name: 'Failures only' }))
    expect(router.state.location.search).toContain('failures=1')
  })

  it('keeps both filters when two change before the page redraws', async () => {
    const { router } = renderApp('/evals/questions')
    await table()

    // One act: no redraw between the two changes, as when a busy browser is slow to redraw.
    act(() => {
      fireEvent.change(screen.getByRole('combobox', { name: 'Suite' }), {
        target: { value: 'chinook' },
      })
      fireEvent.click(screen.getByRole('checkbox', { name: 'Failures only' }))
    })

    await waitFor(() => expect(router.state.location.search).toContain('failures=1'))
    expect(router.state.location.search).toContain('suite=chinook')
  })

  it('reads the filters from the link', async () => {
    renderApp('/evals/questions?study=bird&tier=challenging&database=bird_toxicology')

    expect(await bodyRows()).toHaveLength(4)
    expect(screen.getByRole('combobox', { name: 'Tier' })).toHaveValue('challenging')
    expect(screen.getByRole('combobox', { name: 'Database' })).toHaveValue('bird_toxicology')
  })

  it('offers no database filter where every question shares one database', async () => {
    renderApp('/evals/questions')

    await table()
    expect(screen.queryByRole('combobox', { name: 'Database' })).not.toBeInTheDocument()
  })

  it('ignores a filter value from a mistyped link', async () => {
    renderApp('/evals/questions?tier=impossible')

    expect(await bodyRows()).toHaveLength(questionsOf(data, 'golden').length)
  })

  it('says so when nothing matches, and offers a way back', async () => {
    const bird = questionsOf(data, 'bird')
    const { tiers, databases } = filterOptions(bird)
    const empty = tiers
      .flatMap((tier) => databases.map((database) => ({ tier, database })))
      .find(
        ({ tier, database }) =>
          !bird.some((question) => question.tier === tier && question.database === database),
      )
    expect(empty).toBeDefined()
    const user = userEvent.setup()
    renderApp(`/evals/questions?study=bird&tier=${empty?.tier}&database=${empty?.database}`)

    await user.click(await screen.findByRole('link', { name: 'Clear the filters' }))

    expect(await bodyRows()).toHaveLength(100)
  })
})
