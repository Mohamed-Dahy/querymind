/** An answer's model, key, tokens and cost in words (5.8b), for UsageLine. */
import type { components } from '@/api/schema'

export type LLMUsage = components['schemas']['LLMUsage']

const NUMBER = new Intl.NumberFormat('en')
// A question costs fractions of a cent: two significant digits say enough ("$0.0014").
const DOLLARS = new Intl.NumberFormat('en', {
  style: 'currency',
  currency: 'USD',
  maximumSignificantDigits: 2,
})

/** The usage in words, for the line below and its tests. */
export function usageParts(usage: LLMUsage): string[] {
  const calls = `${usage.llm_calls} AI ${usage.llm_calls === 1 ? 'call' : 'calls'}`
  const tokens =
    usage.input_tokens != null && usage.output_tokens != null
      ? `${NUMBER.format(usage.input_tokens)} + ${NUMBER.format(usage.output_tokens)} tokens`
      : 'tokens not reported'
  // Null: the model isn't on our price list (or a call didn't report its tokens): never a guess.
  const cost =
    usage.cost_usd != null ? `about ${DOLLARS.format(usage.cost_usd)}` : 'price not known'
  return [usage.model, usage.own_key ? 'your key' : 'demo key', calls, tokens, cost]
}
