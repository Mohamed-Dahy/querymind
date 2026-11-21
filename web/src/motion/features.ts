import { domMax } from 'motion/react'

// What motion can do (animations, gestures, exit animations, layout and `layoutId`), loaded on
// demand by MotionProvider: a separate chunk, so it isn't part of the first-load budget
// (scripts/check-bundle.mjs). domMax, not the smaller domAnimation, because the sliding tab and nav
// indicators need `layoutId`.
export default domMax
