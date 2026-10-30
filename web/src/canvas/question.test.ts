import { describe, expect, it } from 'vitest'

import openapi from '../../openapi.json'
import { EXAMPLE_GROUPS } from './examples'
import { checkQuestion, limitAnnouncement, MAX_QUESTION_LENGTH, questionLength } from './question'

describe('the question limit', () => {
  it("matches the API's AskRequest.question maxLength", () => {
    // If the API's limit changes, export_openapi.py rewrites openapi.json and this test says so.
    expect(openapi.components.schemas.AskRequest.properties.question.maxLength).toBe(
      MAX_QUESTION_LENGTH,
    )
  })

  it('counts characters after trimming, like the API', () => {
    expect(questionLength('  hello \n')).toBe(5)
    // One emoji is 2 UTF-16 units in JavaScript but 1 character in Python.
    expect('📈'.length).toBe(2)
    expect(questionLength('sales 📈')).toBe(7)
  })
})

describe('checkQuestion', () => {
  it('returns the trimmed question', () => {
    expect(checkQuestion('  How many customers?  ')).toEqual({
      ok: true,
      question: 'How many customers?',
    })
  })

  it.each(['', '   ', '\n\t'])('refuses an empty question (%j)', (text) => {
    expect(checkQuestion(text)).toEqual({ ok: false, error: 'Type a question first.' })
  })

  it('accepts exactly the limit and refuses one more, saying how much to remove', () => {
    expect(checkQuestion('a'.repeat(MAX_QUESTION_LENGTH)).ok).toBe(true)
    expect(checkQuestion('a'.repeat(MAX_QUESTION_LENGTH + 12))).toEqual({
      ok: false,
      error: 'Questions can be at most 1,000 characters. Remove 12 to send it.',
    })
  })
})

describe('limitAnnouncement', () => {
  it.each([
    [0, ''],
    [899, ''],
    [900, '100 characters or fewer left.'],
    [949, '100 characters or fewer left.'],
    [950, '50 characters or fewer left.'],
    [990, '10 characters or fewer left.'],
    [1000, '10 characters or fewer left.'],
    [1001, 'Over the 1,000-character limit.'],
  ])('at %i characters says %j', (length, expected) => {
    expect(limitAnnouncement(length)).toBe(expected)
  })
})

describe('the example questions', () => {
  const all = EXAMPLE_GROUPS.flatMap((group) => group.questions)

  it('are all sendable and different', () => {
    expect(all.every((question) => checkQuestion(question).ok)).toBe(true)
    expect(new Set(all).size).toBe(all.length)
  })
})
