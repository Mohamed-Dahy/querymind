import { describe, expect, it } from 'vitest'

import { answer, repair } from '@/mocks/fixtures'

import { usageParts, type LLMUsage } from './usage'

const USAGE: LLMUsage = {
  provider: 'groq',
  model: 'openai/gpt-oss-120b',
  own_key: true,
  llm_calls: 2,
  input_tokens: 3301,
  output_tokens: 237,
  cost_usd: 0.000637,
}

describe('usageParts', () => {
  it('says the model, whose key, the calls, the tokens and the list price', () => {
    expect(usageParts(USAGE)).toEqual([
      'openai/gpt-oss-120b',
      'your key',
      '2 AI calls',
      '3,301 + 237 tokens',
      'about $0.00064',
    ])
  })

  it('one call, on the demo key', () => {
    expect(usageParts({ ...USAGE, own_key: false, llm_calls: 1 }).slice(1, 3)).toEqual([
      'demo key',
      '1 AI call',
    ])
  })

  it('a model not on the price list: "price not known", never a guess', () => {
    expect(usageParts({ ...USAGE, cost_usd: null }).at(-1)).toBe('price not known')
  })

  it('tokens a provider did not report are said so, not shown as 0', () => {
    const parts = usageParts({ ...USAGE, input_tokens: null, output_tokens: null, cost_usd: null })
    expect(parts.slice(3)).toEqual(['tokens not reported', 'price not known'])
  })

  it('the recorded replies: a real Gemini answer, and the scripted repair with nothing known', () => {
    expect(usageParts(answer.usage!).join(' · ')).toBe(
      'gemini-3.5-flash-lite · demo key · 2 AI calls · 2,777 + 224 tokens · about $0.0014',
    )
    expect(usageParts(repair.usage!).slice(3)).toEqual(['tokens not reported', 'price not known'])
  })
})
