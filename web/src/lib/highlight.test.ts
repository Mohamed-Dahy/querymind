import { describe, expect, it } from 'vitest'

import { answer } from '@/mocks/fixtures'

import { sqlTokens } from './highlight'

describe('sqlTokens', () => {
  it('colours the recorded SQL with our CSS variables (the grammar runs on the JS engine)', async () => {
    const lines = await sqlTokens(answer.sql)
    const tokens = lines.flat()

    // Nothing lost: the tokens put back together are the SQL itself.
    expect(tokens.map((token) => token.content).join('')).toBe(answer.sql)
    expect(tokens[0]).toMatchObject({ content: 'SELECT', color: 'var(--shiki-token-keyword)' })
    const sum = tokens.find((token) => token.content.trim() === 'SUM')
    expect(sum?.color).toMatch(/^var\(--shiki-/)
  })

  it('keeps line breaks as separate lines', async () => {
    const lines = await sqlTokens("SELECT 1\nFROM t\nWHERE name = '<b>x</b>'")

    expect(lines).toHaveLength(3)
    // Text stays text: SqlView renders it as React children, never as HTML.
    expect(lines[2]?.map((token) => token.content).join('')).toBe("WHERE name = '<b>x</b>'")
  })
})
