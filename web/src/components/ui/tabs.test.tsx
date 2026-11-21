import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { expect, it } from 'vitest'

import { Tabs, TabsContent, TabsList, TabsTrigger } from './tabs'

// The sliding indicator (U8.1): exactly one, inside the active tab, and it follows the active tab
// whether the tabs control themselves (defaultValue) or a parent does (value).

/** The indicator spans: decorative, hidden from screen readers. */
function indicators() {
  return document.querySelectorAll('[data-slot="tabs-trigger"] > span[aria-hidden="true"]')
}

function Evidence(props: {
  defaultValue?: string
  value?: string
  onValueChange?: (v: string) => void
}) {
  return (
    <Tabs {...props}>
      <TabsList>
        <TabsTrigger value="chart">Chart</TabsTrigger>
        <TabsTrigger value="table">Table</TabsTrigger>
      </TabsList>
      <TabsContent value="chart">A chart</TabsContent>
      <TabsContent value="table">A table</TabsContent>
    </Tabs>
  )
}

it('puts the one indicator in the active tab and moves it on click', async () => {
  render(<Evidence defaultValue="chart" />)
  expect(indicators()).toHaveLength(1)
  expect(screen.getByRole('tab', { name: 'Chart' })).toContainElement(
    indicators()[0] as HTMLElement,
  )

  await userEvent.click(screen.getByRole('tab', { name: 'Table' }))
  expect(indicators()).toHaveLength(1)
  expect(screen.getByRole('tab', { name: 'Table' })).toContainElement(
    indicators()[0] as HTMLElement,
  )
  expect(screen.getByRole('tabpanel')).toHaveTextContent('A table')
})

it('follows a parent that controls the value, and still reports changes', async () => {
  function Controlled() {
    const [value, setValue] = useState('table')
    return <Evidence value={value} onValueChange={setValue} />
  }
  render(<Controlled />)
  expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Table' })).toContainElement(
    indicators()[0] as HTMLElement,
  )

  await userEvent.click(screen.getByRole('tab', { name: 'Chart' }))
  expect(screen.getByRole('tab', { name: 'Chart' })).toHaveAttribute('aria-selected', 'true')
  expect(screen.getByRole('tab', { name: 'Chart' })).toContainElement(
    indicators()[0] as HTMLElement,
  )
})

it('keeps each tab name to its words: the indicator adds nothing to it', () => {
  render(<Evidence defaultValue="chart" />)
  expect(screen.getByRole('tab', { name: 'Chart' })).toHaveAccessibleName('Chart')
})
