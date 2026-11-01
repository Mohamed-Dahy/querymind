import { describe, expect, it } from 'vitest'

import { ApiError, ask } from '@/api/client'
import { ERROR_TITLES } from '@/api/errors'
import { askStream, type TraceEvent } from '@/api/stream'
import {
  answer,
  answerStream,
  errors,
  followup,
  followupStream,
  monthly,
  repair,
  repairStream,
  single,
  type MockErrorCode,
} from './fixtures'

// The real client against the fake API: what tests and mock mode get for each kind of question.
describe('mock API', () => {
  it('answers with the recorded answer, for the question that was asked', async () => {
    const reply = await ask({ question: 'Sales per country?' })

    expect(reply).toEqual({
      ...answer,
      question: 'Sales per country?',
      conversation_id: expect.any(String),
    })
  })

  it('starts a new conversation, with a new id, for each question without one', async () => {
    const first = await ask({ question: 'Sales per country?' })
    const second = await ask({ question: 'Delete all orders' })

    expect(first.conversation_id).not.toBe(second.conversation_id)
    expect(first.conversation_id).not.toBe(answer.conversation_id)
  })

  it('continues a conversation with the recorded follow-up', async () => {
    const reply = await ask({ question: 'Only 1997', conversation_id: answer.conversation_id })

    expect(reply).toEqual({
      ...followup,
      question: 'Only 1997',
      conversation_id: answer.conversation_id,
    })
  })

  it('answers "rows: N" with that many made-up rows, at most 1,000', async () => {
    expect(await ask({ question: 'rows: 1000' })).toMatchObject({
      kind: 'answer',
      row_count: 1000,
      truncated: true,
    })
    expect(await ask({ question: 'rows: 5000' })).toMatchObject({ row_count: 1000 })
    expect(await ask({ question: 'rows: 20' })).toMatchObject({ row_count: 20, truncated: false })
  })

  it('answers "per month" with the monthly sales, and "how many customers" with one number', async () => {
    expect(await ask({ question: 'Total sales per month in 1997?' })).toMatchObject({
      columns: monthly.columns,
      rows: monthly.rows,
    })
    expect(await ask({ question: 'How many customers are there?' })).toMatchObject({
      rows: single.rows,
    })
    // A Streamlit example that must still get the per-category answer (a bar chart).
    expect(await ask({ question: 'How many orders did each country place?' })).toMatchObject({
      columns: answer.columns,
    })
  })

  it('refuses to change data and asks back on an ambiguous question', async () => {
    expect((await ask({ question: 'Delete all orders' })).kind).toBe('refusal')
    expect((await ask({ question: 'Who are our best customers?' })).kind).toBe('clarification')
  })

  it.each(Object.keys(errors) as MockErrorCode[])(
    '"error: %s" fails with that code, its status and a UI title',
    async (code) => {
      const failure = await ask({ question: `error: ${code}` }).catch((error: unknown) => error)

      expect(failure).toBeInstanceOf(ApiError)
      expect(failure).toMatchObject({ code, status: errors[code].status })
      // Every code the server sends has its own title in the UI (not the generic fallback).
      expect(ERROR_TITLES).toHaveProperty(code)
    },
  )
})

// The same, for POST /ask/stream: the steps, then the same answer.
describe('mock API, streaming', () => {
  async function stream(question: string, conversation_id?: string) {
    const events: TraceEvent[] = []
    const reply = await askStream(
      conversation_id === undefined ? { question } : { question, conversation_id },
      { onEvent: (event) => events.push(event) },
    )
    return { events, reply }
  }

  it('replays the recorded events, then the answer for the question that was asked', async () => {
    const { events, reply } = await stream('Sales per country?')

    expect(events).toEqual(answerStream)
    expect(reply).toEqual({
      ...answer,
      question: 'Sales per country?',
      conversation_id: expect.any(String),
    })
  })

  it('a follow-up gets the follow-up events, in its conversation', async () => {
    const { events, reply } = await stream('Only 1997', answer.conversation_id)

    expect(events).toEqual(followupStream)
    expect(reply.conversation_id).toBe(answer.conversation_id)
  })

  it('"in each country" gets the repair; a Streamlit example does not', async () => {
    const repaired = await stream('How many customers are there in each country?')
    expect(repaired.events).toEqual(repairStream)
    expect(repaired.reply).toMatchObject({ attempts: repair.attempts, sql_attempts: 2 })

    const plain = await stream('How many orders did each country place?')
    expect(plain.events).toEqual(answerStream)
  })

  it.each(Object.keys(errors) as MockErrorCode[])(
    '"error: %s" sends the first steps, then fails with that code',
    async (code) => {
      const events: TraceEvent[] = []

      await expect(
        askStream({ question: `error: ${code}` }, { onEvent: (event) => events.push(event) }),
      ).rejects.toMatchObject({ code, message: errors[code].message })
      expect(events).toEqual(answerStream.slice(0, 4))
    },
  )

  it('"stream: broken" fails at once, so the answer comes from POST /ask', async () => {
    const { events, reply } = await stream('stream: broken')

    expect(events).toEqual([])
    expect(reply).toMatchObject({ kind: 'answer', question: 'stream: broken' })
  })
})
