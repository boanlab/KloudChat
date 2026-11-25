/** What went wrong with one clip of a reading, as far as the person can do anything about it. */
import { ApiError } from './api'

export type SpeakProblem = '' | 'limit' | 'credits' | 'unavailable' | 'playback' | 'failed'

/** A clip that did not come: one the rest of the reading cannot get past (`stops`), or just that block. */
export function speakProblem(error: unknown): { problem: SpeakProblem; stops: boolean } {
  if (error instanceof ApiError) {
    if (error.status === 429) return { problem: 'limit', stops: true }
    if (error.status === 503) return { problem: 'unavailable', stops: true }
    if (error.status === 422 && error.detail === 'insufficient_credits') return { problem: 'credits', stops: true }
  }
  return { problem: 'failed', stops: false }
}

/** Blocks in a row that fail before the rest of the reading is given up. */
export const STRIKES = 3

export const aborted = (error: unknown) => error instanceof DOMException && error.name === 'AbortError'

/** A browser that will not play sound until the person has touched the page. */
export const playbackRefused = (error: unknown) => error instanceof DOMException && error.name === 'NotAllowedError'

const SILENCE =
  'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YQAAAAA='

/** Called inside a click: a silent clip played now lets the clips that follow (made after a wait) play. */
export function unlockAudio() {
  try {
    void new Audio(SILENCE).play().catch(() => undefined)
  } catch {
    // no audio element: nothing to unlock
  }
}
