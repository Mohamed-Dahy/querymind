import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MotionGlobalConfig } from 'motion/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'

import { CountUp, DrawPath, MotionProvider, Pop, Reveal, Stagger, StaggerItem, Swap } from '.'
import { exitDuration } from './tokens'

// src/test/setup.ts switches animations off for every test: the primitives must then render their
// end state at once, with no hidden or half-faded content. The second block switches them on, to
// check the animated path really starts from "hidden".

describe('with animations off (unit tests, reduced motion)', () => {
  it('Reveal is a plain element, fully visible', () => {
    render(
      <MotionProvider>
        <Reveal as="section" aria-label="Answer">
          London fell 18.4%.
        </Reveal>
      </MotionProvider>,
    )
    const section = screen.getByRole('region', { name: 'Answer' })
    expect(section.tagName).toBe('SECTION')
    expect(section).toHaveTextContent('London fell 18.4%.')
    expect(section.style.opacity).toBe('')
    expect(section).toBeVisible()
  })

  it('Stagger renders every item at once', () => {
    render(
      <Stagger as="ul" aria-label="Examples">
        {['one', 'two', 'three'].map((item) => (
          <StaggerItem as="li" key={item}>
            {item}
          </StaggerItem>
        ))}
      </Stagger>,
    )
    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(3)
    for (const item of items) expect(item.style.opacity).toBe('')
  })

  it('CountUp shows the real value straight away, formatted', () => {
    render(<CountUp value={57} format={(n) => `${Math.round(n)}%`} />)
    expect(screen.getByText('57%')).toHaveClass('tabular-nums')
  })

  it('Swap shows the new content as soon as its id changes', async () => {
    function Toggle() {
      const [id, setId] = useState('answer')
      return (
        <>
          <button onClick={() => setId('sql')}>Show SQL</button>
          <Swap id={id}>{id === 'answer' ? 'The answer' : 'SELECT 1;'}</Swap>
        </>
      )
    }
    render(<Toggle />)
    expect(screen.getByText('The answer')).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Show SQL' }))
    expect(screen.getByText('SELECT 1;')).toBeVisible()
    expect(screen.queryByText('The answer')).not.toBeInTheDocument()
  })

  it('Pop is a plain panel, already open', () => {
    render(<Pop data-testid="panel">Settings</Pop>)
    const panel = screen.getByTestId('panel')
    expect(panel.style.opacity).toBe('')
    expect(panel.style.transform).toBe('')
    expect(panel).toBeVisible()
  })

  it('DrawPath draws the whole path, with no dash trick left on it', () => {
    const { container } = render(
      <svg>
        <DrawPath d="M0 0 L10 10" stroke="currentColor" />
      </svg>,
    )
    const path = container.querySelector('path')
    expect(path).toHaveAttribute('d', 'M0 0 L10 10')
    expect(path).not.toHaveAttribute('stroke-dasharray')
  })
})

describe('with animations on', () => {
  afterEach(() => {
    MotionGlobalConfig.skipAnimations = true
  })

  it('Reveal starts hidden and lowered, then animates in', () => {
    MotionGlobalConfig.skipAnimations = false
    render(<Reveal data-testid="reveal">Arriving</Reveal>)
    const element = screen.getByTestId('reveal')
    expect(element.style.opacity).toBe('0')
    expect(element.style.transform).toContain('translateY(10px)')
  })

  it('Pop grows from the corner of its button', () => {
    MotionGlobalConfig.skipAnimations = false
    render(<Pop data-testid="panel">Settings</Pop>)
    const panel = screen.getByTestId('panel')
    expect(panel.style.opacity).toBe('0')
    expect(panel.style.transformOrigin).toBe('top right')
  })

  it('CountUp falls back to the value where there is no IntersectionObserver (jsdom)', () => {
    MotionGlobalConfig.skipAnimations = false
    render(<CountUp value={42} from={0} />)
    expect(screen.getByText('42')).toBeInTheDocument()
  })
})

describe('tokens', () => {
  it('makes leaving about two thirds as long as entering', () => {
    expect(exitDuration(0.22)).toBeCloseTo(0.143, 3)
    expect(exitDuration(0.3)).toBeLessThan(0.3)
  })
})
