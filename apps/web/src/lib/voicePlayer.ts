/** Plays an answer's blocks one after another while the next ones are still being voiced. */
import { aborted, playbackRefused, speakProblem, STRIKES, type SpeakProblem } from './speakProblems'
import type { SpokenBlock } from './speechBlocks'

/** Sound left in the room after a clip ends. */
const TAIL_MS = 400

export interface VoicePlayer {
  /** Voices a block (at once, one request at a time) and queues it for playing. */
  add: (block: SpokenBlock) => void
  /** Stops what is playing and drops what is queued. */
  stop: () => void
  /** Sound is playing or queued. */
  busy: () => boolean
  /** A clip is audible now, or just stopped (its tail is still in the room): the microphone hears it. */
  sounding: () => boolean
  /** The rest of this answer is not voiced: the speech server failed, refused, or the browser would not play. */
  failed: () => boolean
  /** Why, when it is: for the line the person reads. */
  problem: () => SpeakProblem
}

export function createVoicePlayer(
  synthesize: (block: SpokenBlock, signal: AbortSignal) => Promise<Blob>,
  onChange: () => void,
): VoicePlayer {
  let epoch = 0
  let queue: Promise<string | null>[] = []
  let chain: Promise<unknown> = Promise.resolve()
  let current: HTMLAudioElement | null = null
  let playing = false
  let endCurrent: (() => void) | null = null
  let broke = false
  let why: SpeakProblem = ''
  /** Blocks in a row that did not come. */
  let strikes = 0
  let abort = new AbortController()
  let endedAt = 0
  const urls = new Set<string>()

  const release = (url: string | null) => {
    if (!url) return
    URL.revokeObjectURL(url)
    urls.delete(url)
  }

  const pump = async (mine: number) => {
    if (playing) return
    playing = true
    onChange()
    while (queue.length && mine === epoch) {
      const url = await queue.shift()!
      if (!url || mine !== epoch) {
        release(url)
        continue
      }
      await new Promise<void>((done) => {
        const audio = new Audio(url)
        current = audio
        const end = () => {
          endedAt = Date.now()
          if (current === audio) current = null
          if (endCurrent === end) endCurrent = null
          release(url)
          done()
        }
        endCurrent = end
        audio.onended = end
        audio.onerror = end
        audio.play().catch((error) => {
          // The browser would not play without a touch: it will not for the rest either.
          if (playbackRefused(error) && mine === epoch) {
            broke = true
            why = 'playback'
            queue = []
            onChange()
          }
          end()
        })
      })
    }
    if (mine === epoch) playing = false
    onChange()
  }

  return {
    add: (block) => {
      if (broke) return
      const mine = epoch
      const signal = abort.signal
      const clip = chain.then(async () => {
        if (mine !== epoch) return null
        try {
          const url = URL.createObjectURL(await synthesize(block, signal))
          if (mine !== epoch) {
            URL.revokeObjectURL(url)
            return null
          }
          strikes = 0
          urls.add(url)
          return url
        } catch (error) {
          if (mine !== epoch || aborted(error)) return null
          // A block that fails is skipped; the written answer is untouched. A refusal the rest
          // would meet too, or three misses in a row, ends the voice for this answer.
          const { problem, stops } = speakProblem(error)
          strikes += 1
          if (stops || strikes >= STRIKES) {
            broke = true
            why = problem
            onChange()
          }
          return null
        }
      })
      chain = clip
      queue.push(clip)
      void pump(mine)
    },
    stop: () => {
      epoch += 1
      abort.abort()
      abort = new AbortController()
      queue = []
      chain = Promise.resolve()
      broke = false
      why = ''
      strikes = 0
      playing = false
      current?.pause()
      current = null
      endCurrent?.()
      endCurrent = null
      urls.forEach((url) => URL.revokeObjectURL(url))
      urls.clear()
      onChange()
    },
    busy: () => playing || queue.length > 0,
    // Waiting for the speech server makes the player busy but silent: the microphone then needs no
    // higher bar.
    sounding: () => (current !== null && !current.paused && !current.ended) || Date.now() - endedAt < TAIL_MS,
    failed: () => broke,
    problem: () => why,
  }
}
