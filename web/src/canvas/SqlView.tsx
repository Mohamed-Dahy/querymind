import { Copy } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { ThemedToken } from 'shiki/core'

import { Button } from '@/components/ui/button'

type Highlighted = { sql: string; lines: ThemedToken[][] }
type CopyStatus = 'idle' | 'copied' | 'failed'

const COPY_MESSAGES: Record<CopyStatus, string> = {
  idle: '',
  copied: 'SQL copied.',
  failed: "Couldn't copy. Select the SQL and press Ctrl+C.",
}

/** The SQL's coloured tokens, once Shiki has loaded; null until then (or if it fails). */
function useSqlTokens(sql: string): ThemedToken[][] | null {
  const [highlighted, setHighlighted] = useState<Highlighted | null>(null)

  useEffect(() => {
    let current = true
    // A dynamic import: Shiki becomes its own file, downloaded the first time a SQL tab opens.
    import('@/lib/highlight')
      .then(({ sqlTokens }) => sqlTokens(sql))
      .then((lines) => {
        if (current) setHighlighted({ sql, lines })
      })
      // Without colours the plain SQL is still there: nothing to tell the user.
      .catch(() => {})
    return () => {
      current = false
    }
  }, [sql])

  return highlighted?.sql === sql ? highlighted.lines : null
}

type SqlViewProps = { sql: string; explanation: string }

/** The SQL that ran (coloured), what it does in plain words, and a copy button. */
export function SqlView({ sql, explanation }: SqlViewProps) {
  const lines = useSqlTokens(sql)
  const [copyStatus, setCopyStatus] = useState<CopyStatus>('idle')

  useEffect(() => {
    if (copyStatus === 'idle') return
    const timer = setTimeout(() => setCopyStatus('idle'), 3000)
    return () => clearTimeout(timer)
  }, [copyStatus])

  async function copy() {
    try {
      // navigator.clipboard only exists on HTTPS or localhost; elsewhere this throws.
      await navigator.clipboard.writeText(sql)
      setCopyStatus('copied')
    } catch {
      setCopyStatus('failed')
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-fg-muted">{explanation}</p>
      {/* Plain text first, the same size: no jump when the colours arrive. */}
      <pre className="overflow-x-auto rounded-lg bg-muted p-3 font-mono text-sm leading-relaxed break-words whitespace-pre-wrap">
        <code>
          {lines
            ? lines.map((line, lineIndex) => (
                <span key={lineIndex}>
                  {lineIndex > 0 && '\n'}
                  {line.map((token, tokenIndex) => (
                    // React text, never HTML: a '<' in the SQL stays a '<'.
                    <span key={tokenIndex} style={{ color: token.color }}>
                      {token.content}
                    </span>
                  ))}
                </span>
              ))
            : sql}
        </code>
      </pre>
      <div className="flex flex-wrap items-center gap-3">
        <Button type="button" variant="outline" onClick={() => void copy()}>
          <Copy aria-hidden="true" />
          Copy SQL
        </Button>
        <span aria-live="polite" className="text-sm text-fg-muted">
          {COPY_MESSAGES[copyStatus]}
        </span>
      </div>
    </div>
  )
}
