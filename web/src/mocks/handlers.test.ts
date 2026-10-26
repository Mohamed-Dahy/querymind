import { describe, expect, it } from 'vitest'

import { ApiError, ask } from '@/api/client'
import { ERROR_TITLES } from '@/api/errors'
import { answer, errors, followup, type MockErrorCode } from './fixtures'

// The real client against the fake API: what tests and mock mode get for each kind of question.
describe('mock API', () => {
  it('answers with the recorded answer, for the question that was asked', async () => {
    const reply = await ask({ question: 'Sales per country?' })

    expect(reply).toEqual({ ...answer, question: 'Sales per country?' })
  })

  it('continues a conversation with the recorded follow-up', async () => {
    const reply = await ask({ question: 'Only 1997', conversation_id: answer.conversation_id })

    expect(reply).toEqual({
      ...followup,
      question: 'Only 1997',
      conversation_id: answer.conversation_id,
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
