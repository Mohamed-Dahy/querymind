/**
 * A word-level diff: what changed from one SQL try to the next. Small on purpose, for short texts.
 *
 * It finds the longest common subsequence: the longest run of words both texts share in the same
 * order. Whatever isn't in it was removed from the first text or added in the second.
 */
export type DiffPart = { kind: 'same' | 'added' | 'removed'; text: string }

// Above this many table cells, the texts aren't worth comparing word by word: all replaced.
const MAX_CELLS = 1_000_000

function tokens(text: string): string[] {
  return text.match(/\s+|[\p{L}\p{N}_]+|[^\s\p{L}\p{N}_]/gu) ?? []
}

/** The parts in reading order. The `same` and `removed` parts make `before`; `same` + `added`, `after`. */
export function diffWords(before: string, after: string): DiffPart[] {
  const a = tokens(before)
  const b = tokens(after)
  if (a.length * b.length > MAX_CELLS) {
    const parts: DiffPart[] = []
    if (before) parts.push({ kind: 'removed', text: before })
    if (after) parts.push({ kind: 'added', text: after })
    return parts
  }

  // lengths[i][j]: how many tokens a[i…] and b[j…] have in common.
  const lengths = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  )
  const at = (i: number, j: number) => lengths[i]?.[j] ?? 0
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      const row = lengths[i] as number[]
      row[j] = a[i] === b[j] ? at(i + 1, j + 1) + 1 : Math.max(at(i + 1, j), at(i, j + 1))
    }
  }

  const parts: DiffPart[] = []
  const add = (kind: DiffPart['kind'], text: string) => {
    const last = parts.at(-1)
    if (last?.kind === kind) last.text += text
    else parts.push({ kind, text })
  }
  let i = 0
  let j = 0
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      add('same', a[i] as string)
      i += 1
      j += 1
    } else if (i < a.length && (j === b.length || at(i + 1, j) >= at(i, j + 1))) {
      add('removed', a[i] as string)
      i += 1
    } else {
      add('added', b[j] as string)
      j += 1
    }
  }
  return parts
}
