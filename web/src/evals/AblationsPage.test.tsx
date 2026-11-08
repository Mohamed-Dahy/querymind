import { screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { AblationArm } from '@/api/evals'
import { evals } from '@/mocks/fixtures'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const golden = () =>
  screen.findByRole('region', { name: 'Golden 58: Northwind 30 + business terms 13 + Chinook 15' })
const bird = () =>
  screen.findByRole('region', { name: 'BIRD-100: the fixed stratified subset, 11 databases' })

/** Serve the real results with some arms of one study changed (for states the real runs no longer show). */
const serveEdited = (study: string, edit: (arm: AblationArm, scope: string) => AblationArm) => {
  const ablations = evals.ablations.map((s) =>
    s.name !== study
      ? s
      : { ...s, views: s.views.map((v) => ({ ...v, arms: v.arms.map((a) => edit(a, v.scope)) })) },
  )
  server.use(http.get('/api/evals', () => HttpResponse.json({ ...evals, ablations })))
}

describe('AblationsPage', () => {
  it('says every bar in words, so it reads with colour removed', async () => {
    renderApp('/evals/ablations')

    const section = within(await golden())
    const arm = (key: string) => section.getByRole('listitem', { name: `Arm ${key}` })
    expect(arm('A')).toHaveTextContent('45/58')
    expect(arm('A')).toHaveTextContent('77.6%')
    expect(arm('B')).toHaveTextContent('no change')
    expect(arm('C')).toHaveTextContent('57/58')
    expect(arm('C')).toHaveTextContent('+20.7 pts')
    expect(arm('C')).not.toHaveTextContent('within noise')
    expect(arm('D')).toHaveTextContent('58/58')
    expect(arm('D')).toHaveTextContent('+1.7 pts')
    expect(arm('D')).toHaveTextContent('within noise')
    expect(arm('D')).not.toHaveTextContent('not scored')
  })

  it('has no warning when every arm was scored on every question', async () => {
    renderApp('/evals/ablations')

    expect(await golden()).not.toHaveTextContent('Not every arm was scored')
  })

  it('warns when the arms were scored on different questions', async () => {
    serveEdited('golden', (arm, scope) =>
      scope === 'all' && arm.key === 'D'
        ? { ...arm, match: { passed: 53, total: 53 }, not_scored: 5 }
        : arm,
    )
    renderApp('/evals/ablations')

    const section = await golden()
    expect(section).toHaveTextContent('Not every arm was scored on the same questions')
    expect(within(section).getByRole('listitem', { name: 'Arm D' })).toHaveTextContent(
      '5 not scored',
    )
  })

  it('says what the last part saves', async () => {
    renderApp('/evals/ablations')

    expect(await golden()).toHaveTextContent('Arm D reads 49% fewer input tokens')
  })

  it('has a summary for screen readers', async () => {
    renderApp('/evals/ablations')

    expect(await golden()).toHaveTextContent('Arm C 57/58 (98.3%), +20.7 pts;')
  })

  it('has every number in a table too, matching the chart', async () => {
    renderApp('/evals/ablations')

    const table = await screen.findByRole('region', { name: /^Golden 58.*every number/ })
    const row = (name: RegExp) => within(table).getByRole('row', { name })
    expect(row(/^A plain LLM/)).toHaveTextContent('45/58 (77.6%)')
    expect(row(/^A plain LLM/)).toHaveTextContent('42/58 (72.4%)')
    expect(row(/^A plain LLM/)).toHaveTextContent('4,923 + 130')
    expect(row(/^B /)).toHaveTextContent('0.03')
    expect(row(/^C /)).toHaveTextContent('+20.7 pts')
    expect(row(/^C /)).toHaveTextContent('6,724 + 141')
    expect(row(/^D /)).toHaveTextContent('58/58 (100.0%)')
    expect(row(/^D /)).toHaveTextContent('56/58 (96.6%)')
    expect(row(/^D /)).toHaveTextContent('3,454 + 141')
    expect(row(/^D /)).toHaveTextContent('1.9')
  })

  it('switches to one suite, in the URL, and Back returns', async () => {
    const user = userEvent.setup()
    renderApp('/evals/ablations')

    const section = within(await golden())
    await user.click(section.getByRole('link', { name: 'Business terms 13' }))

    const after = within(await golden())
    expect(after.getByRole('link', { name: 'Business terms 13' })).toHaveAttribute(
      'aria-current',
      'true',
    )
    expect(after.getByRole('listitem', { name: 'Arm A' })).toHaveTextContent('6/13')
    expect(after.getByRole('listitem', { name: 'Arm C' })).toHaveTextContent('13/13')
    expect(after.getByRole('listitem', { name: 'Arm C' })).toHaveTextContent('+53.8 pts')
    expect(after.getByRole('listitem', { name: 'Arm C' })).not.toHaveTextContent('within noise')
    expect(after.queryByText(/Not every arm was scored/)).not.toBeInTheDocument()
  })

  it('ignores a scope it does not know', async () => {
    renderApp('/evals/ablations?scope=nonsense')

    expect(within(await golden()).getByRole('listitem', { name: 'Arm A' })).toHaveTextContent(
      '45/58',
    )
  })

  it('BIRD: the hints are the big step, self-correction adds nothing', async () => {
    renderApp('/evals/ablations')

    const section = within(await bird())
    const arm = (key: string) => section.getByRole('listitem', { name: `Arm ${key}` })
    expect(arm('A')).toHaveTextContent('40/100')
    expect(arm('B')).toHaveTextContent('+17.0 pts')
    expect(arm('C')).toHaveTextContent('no change')

    const table = within(section.getByRole('region', { name: /every number/ }))
    expect(table.getByRole('row', { name: /^A plain LLM/ })).toHaveTextContent('51/100 (51.0%)')
    expect(table.getByRole('row', { name: /^A plain LLM/ })).toHaveTextContent('40/100 (40.0%)')
  })

  it("BIRD with one arm run: no one-bar chart, and says A and B haven't run, never a zero", async () => {
    serveEdited('bird', (arm) =>
      arm.key === 'C'
        ? arm
        : { ...arm, match: null, ex: null, delta_points: null, missing: ['bird'] },
    )
    renderApp('/evals/ablations')

    const section = within(await bird())
    expect(section.queryByRole('listitem', { name: 'Arm C' })).not.toBeInTheDocument()
    expect(await bird()).toHaveTextContent('Only arm C has run')
    expect(await bird()).toHaveTextContent('57/100 by BIRD EX')
    expect(await bird()).toHaveTextContent('Not run yet: arm A, arm B.')

    const table = within(section.getByRole('region', { name: /every number/ }))
    expect(table.getByRole('row', { name: /^A plain LLM/ })).toHaveTextContent('not run yet')
    expect(table.getByRole('row', { name: /^A plain LLM/ })).not.toHaveTextContent('0/')
    expect(table.getByRole('row', { name: /^C / })).toHaveTextContent('63/100 (63.0%)')
    expect(table.getByRole('row', { name: /^C / })).toHaveTextContent('57/100 (57.0%)')
  })

  it('names the reports behind the numbers', async () => {
    renderApp('/evals/ablations?scope=chinook')

    const section = await golden()
    expect(section).toHaveTextContent('phase4-ablation-golden-D-chinook')
    expect(section).not.toHaveTextContent('phase4-ablation-golden-D-business')
  })

  it('reaches every view from the sub-menu', async () => {
    const user = userEvent.setup()
    renderApp('/evals')

    const menu = () => screen.findByRole('navigation', { name: 'Evals lab views' })
    await user.click(within(await menu()).getByRole('link', { name: 'What each part adds' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'What each part adds' }),
    ).toBeInTheDocument()
    expect(within(await menu()).getByRole('link', { name: 'What each part adds' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(within(await menu()).getByRole('link', { name: 'Scoreboard' })).not.toHaveAttribute(
      'aria-current',
    )
  })
})
