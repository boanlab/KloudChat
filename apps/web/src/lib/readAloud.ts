/** Reads a written text aloud, one block after another: a chat answer, a report, a deck's notes.
 * The text on the screen is not touched; each block goes to the self-hosted voice, which puts it into
 * spoken words first (`/audio/speak`, the same path the voice conversation uses). One reading at a time;
 * the next blocks are voiced while one plays, but only a couple ahead, so stopping wastes little. */
import { useSyncExternalStore } from 'react'
import { ApiError, speakApi } from './api'
import { BlockFeed, type SpokenBlock } from './speechBlocks'

/** A part of a document read as one: a section, a slide, a whole answer. */
export interface ReadUnit {
  /** Shown while this part is read (a section's heading, 「3번 슬라이드」). */
  label?: string
  /** Handed back when this part starts (`onAt`), to scroll to it. */
  at?: string
  /** Read first, as a heading. */
  heading?: string
  /** Markdown or plain lines. */
  text: string
}

export interface ReadPiece {
  block: SpokenBlock
  label: string
  at: string
}

/** More than this is not read at once; the rest is left with a line saying so. */
export const READ_MAX_CHARS = 20_000
/** Blocks voiced ahead of the one playing. */
const AHEAD = 2
const PROBLEM_MS = 5000

/** The blocks of `units` in order; `cut` when `max` characters came before the end. */
export function readPieces(units: ReadUnit[], max = READ_MAX_CHARS): { pieces: ReadPiece[]; cut: boolean } {
  const pieces: ReadPiece[] = []
  let chars = 0
  for (const unit of units) {
    const body = unit.text.trim()
    const heading = (unit.heading ?? '').trim()
    if (!body && !heading) continue
    const markdown = (heading ? `## ${heading}\n\n` : '') + body
    for (const block of new BlockFeed().push(markdown, true)) {
      chars += block.source.length || block.text.length
      if (chars > max) return { pieces, cut: true }
      pieces.push({ block, label: unit.label ?? heading, at: unit.at ?? '' })
    }
  }
  return { pieces, cut: false }
}

export type ReadStatus = 'idle' | 'loading' | 'playing' | 'paused'

export interface ReadState {
  /** Which button owns the reading. */
  key: string | null
  status: ReadStatus
  /** The block being read (0-based) and how many there are. */
  index: number
  total: number
  label: string
  /** Only the first part of a long text is read. */
  cut: boolean
  /** The reading ended early: the speech server was busy (`limit`) or failed. */
  problem: '' | 'limit' | 'failed'
}

const IDLE: ReadState = { key: null, status: 'idle', index: 0, total: 0, label: '', cut: false, problem: '' }

let state: ReadState = IDLE
const subscribers = new Set<() => void>()
const publish = (next: ReadState) => {
  state = next
  subscribers.forEach((fn) => fn())
}

export const readState = () => state
/** Which button owns the reading now. */
export const readingKey = () => state.key

export function useReadState(): ReadState {
  return useSyncExternalStore(
    (fn) => {
      subscribers.add(fn)
      return () => subscribers.delete(fn)
    },
    () => state,
  )
}

let run = 0
let audio: HTMLAudioElement | null = null
let endClip: (() => void) | null = null
let clips = new Map<number, Promise<string>>()
let problemTimer: ReturnType<typeof setTimeout> | null = null
let whenEnded: (() => void) | null = null

function dropClips() {
  for (const clip of clips.values()) clip.then((url) => URL.revokeObjectURL(url)).catch(() => undefined)
  clips = new Map()
}

/** Ends the reading at once: the sound, what is voiced ahead, the button's state. */
export function stopReading() {
  run += 1
  audio?.pause()
  audio = null
  endClip?.()
  endClip = null
  dropClips()
  if (problemTimer) clearTimeout(problemTimer)
  problemTimer = null
  const ended = whenEnded
  whenEnded = null
  ended?.()
  publish(IDLE)
}

function giveUp(key: string, error: unknown) {
  const problem = error instanceof ApiError && error.status === 429 ? 'limit' : 'failed'
  stopReading()
  publish({ ...IDLE, key, problem })
  problemTimer = setTimeout(() => {
    problemTimer = null
    if (state.problem) publish(IDLE)
  }, PROBLEM_MS)
}

export function pauseReading() {
  if (state.status !== 'playing') return
  audio?.pause()
  publish({ ...state, status: 'paused' })
}

export function resumeReading() {
  if (state.status !== 'paused') return
  publish({ ...state, status: 'playing' })
  void audio?.play().catch(() => undefined)
}

const playClip = (url: string, mine: number) =>
  new Promise<void>((done) => {
    const clip = new Audio(url)
    audio = clip
    const end = () => {
      if (audio === clip) audio = null
      if (endClip === end) endClip = null
      done()
    }
    endClip = end
    clip.onended = end
    clip.onerror = end
    if (mine === run) publish({ ...state, status: 'playing' })
    clip.play().catch(end)
  })

/** Starts reading `pieces` for the button `key`; whatever was being read stops. */
export function startReading(
  key: string,
  pieces: ReadPiece[],
  opts: { voice: string; cut?: boolean; onAt?: (at: string) => void; onEnd?: () => void },
) {
  stopReading()
  if (!pieces.length) return
  const mine = (run += 1)
  whenEnded = opts.onEnd ?? null
  publish({ key, status: 'loading', index: 0, total: pieces.length, label: pieces[0].label, cut: opts.cut === true, problem: '' })

  const clip = (i: number) => {
    let made = clips.get(i)
    if (!made) {
      const { block } = pieces[i]
      made = speakApi
        .speak(block.text, opts.voice, { spoken: block.source !== '', source: block.source })
        .then((blob) => URL.createObjectURL(blob))
      made.catch(() => undefined)
      clips.set(i, made)
    }
    return made
  }

  void (async () => {
    for (let i = 0; i < pieces.length; i += 1) {
      if (mine !== run) return
      for (let ahead = 0; ahead <= AHEAD && i + ahead < pieces.length; ahead += 1) void clip(i + ahead)
      publish({ ...state, status: state.status === 'paused' ? 'paused' : 'loading', index: i, label: pieces[i].label })
      opts.onAt?.(pieces[i].at)
      let url: string
      try {
        url = await clip(i)
      } catch (error) {
        if (mine === run) giveUp(key, error)
        return
      }
      if (mine !== run) return
      await playClip(url, mine)
      clips.delete(i)
      URL.revokeObjectURL(url)
    }
    if (mine === run) stopReading()
  })()
}

// A reading does not go on in a hidden tab or a page being left.
if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && state.key) stopReading()
  })
  window.addEventListener('pagehide', () => stopReading())
}
