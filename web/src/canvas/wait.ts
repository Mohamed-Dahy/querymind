function unit(count: number, name: string): string {
  return `${count} ${name}${count === 1 ? '' : 's'}`
}

/**
 * A wait in words: "18 seconds", "7 minutes 40 seconds", "2 hours 5 minutes". Gemini's and Groq's
 * daily limits can mean hours, and nobody needs the seconds then.
 */
export function formatWait(totalSeconds: number): string {
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60
  if (hours > 0) {
    return minutes > 0 ? `${unit(hours, 'hour')} ${unit(minutes, 'minute')}` : unit(hours, 'hour')
  }
  if (minutes > 0) {
    return seconds > 0
      ? `${unit(minutes, 'minute')} ${unit(seconds, 'second')}`
      : unit(minutes, 'minute')
  }
  return unit(seconds, 'second')
}
