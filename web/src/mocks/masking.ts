import type { AskResponse } from '@/api/client'

import { answer } from './fixtures'
import { currentMockUser } from './session'

/**
 * Mock mode: a question about customer contacts (U7.3). Contact names and titles are personal data
 * on the demo (semantic_layer/northwind.yaml), so, as the real API does (PLAN 5.4), every role but
 * admin gets "***" in those columns and `masked_columns` saying which. Northwind's own sample rows.
 */
export const CONTACTS = /\bcontacts?\b/i

const GERMAN_CUSTOMERS: readonly (readonly [string, string, string, string])[] = [
  ['Alfreds Futterkiste', 'Maria Anders', 'Sales Representative', 'Berlin'],
  ['Blauer See Delikatessen', 'Hanna Moos', 'Sales Representative', 'Mannheim'],
  ['Drachenblut Delikatessen', 'Sven Ottlieb', 'Order Administrator', 'Aachen'],
  ['Frankenversand', 'Peter Franken', 'Marketing Manager', 'München'],
  ['Königlich Essen', 'Philip Cramer', 'Sales Associate', 'Brandenburg'],
  ['Lehmanns Marktstand', 'Renate Messner', 'Sales Representative', 'Frankfurt a.M.'],
  ['Morgenstern Gesundkost', 'Alexander Feuer', 'Marketing Assistant', 'Leipzig'],
  ['Ottilies Käseladen', 'Henriette Pfalzheim', 'Owner', 'Köln'],
  ['QUICK-Stop', 'Horst Kloss', 'Accounting Manager', 'Cunewalde'],
  ['Toms Spezialitäten', 'Karin Josephs', 'Marketing Manager', 'Münster'],
  ['Die Wandernde Kuh', 'Rita Müller', 'Sales Representative', 'Stuttgart'],
]
const PERSONAL = [1, 2] // contact_name, contact_title

export function contactsAnswer(): AskResponse {
  const masked = currentMockUser().role !== 'admin'
  const rows = GERMAN_CUSTOMERS.map((row) =>
    row.map((value, index) => (masked && PERSONAL.includes(index) ? '***' : value)),
  )
  return {
    ...answer,
    answer: masked
      ? 'Germany has 11 customers. Their contact names and titles are hidden for your role.'
      : 'Germany has 11 customers, from Alfreds Futterkiste in Berlin to Die Wandernde Kuh in Stuttgart.',
    sql:
      'SELECT company_name, contact_name, contact_title, city FROM customers ' +
      "WHERE country = 'Germany' ORDER BY company_name LIMIT 1001",
    explanation: 'Lists the customers in Germany with their contact person and city.',
    columns: ['company_name', 'contact_name', 'contact_title', 'city'],
    rows,
    row_count: rows.length,
    masked_columns: masked ? PERSONAL : [],
    tables_used: ['customers'],
  }
}
