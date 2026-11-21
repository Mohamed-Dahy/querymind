import { render, screen } from '@testing-library/react'
import { MotionGlobalConfig } from 'motion/react'
import { afterEach, expect, it, vi } from 'vitest'

import { Reveal, Stagger, StaggerItem } from '.'

// A visitor whose system asks for reduced motion. Animations are otherwise allowed here, so this
// checks the reduced-motion switch on its own, not the tests' global "off" switch.
vi.mock('motion/react', async (importOriginal) => ({
  ...(await importOriginal<typeof import('motion/react')>()),
  useReducedMotion: () => true,
}))

afterEach(() => {
  MotionGlobalConfig.skipAnimations = true
})

it('under reduced motion nothing starts hidden: no fade, no rise', () => {
  MotionGlobalConfig.skipAnimations = false
  render(
    <>
      <Reveal data-testid="reveal">Answer</Reveal>
      <Stagger as="ul">
        <StaggerItem as="li">First</StaggerItem>
      </Stagger>
    </>,
  )
  for (const element of [screen.getByTestId('reveal'), screen.getByRole('listitem')]) {
    expect(element.style.opacity).toBe('')
    expect(element.style.transform).toBe('')
  }
})
