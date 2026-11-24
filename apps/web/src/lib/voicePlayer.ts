/** Plays an answer's sentences one after another while the next ones are still being voiced. */

export interface VoicePlayer {
  /** Voices a sentence (at once, one request at a time) and queues it for playing. */
  add: (text: string) => void
  /** Stops what is playing and drops what is queued. */
  stop: () => void
  /** Sound is playing or queued. */
  busy: () => boolean
  /** The speech server failed for this answer; the rest is not voiced. */
  failed: () => boolean
}

export function createVoicePlayer(
  synthesize: (text: string) => Promise<Blob>,
  onChange: () => void,
): VoicePlayer {
  let epoch = 0
  let queue: Promise<string | null>[] = []
  let chain: Promise<unknown> = Promise.resolve()
  let current: HTMLAudioElement | null = null
  let playing = false
  let endCurrent: (() => void) | null = null
  let broke = false
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
          if (current === audio) current = null
          if (endCurrent === end) endCurrent = null
          release(url)
          done()
        }
        endCurrent = end
        audio.onended = end
        audio.onerror = end
        audio.play().catch(end)
      })
    }
    if (mine === epoch) playing = false
    onChange()
  }

  return {
    add: (text) => {
      if (broke) return
      const mine = epoch
      const clip = chain.then(async () => {
        if (mine !== epoch) return null
        try {
          const url = URL.createObjectURL(await synthesize(text))
          if (mine !== epoch) {
            URL.revokeObjectURL(url)
            return null
          }
          urls.add(url)
          return url
        } catch {
          // One failed sentence ends the voice for this answer; the written answer is untouched.
          broke = true
          onChange()
          return null
        }
      })
      chain = clip
      queue.push(clip)
      void pump(mine)
    },
    stop: () => {
      epoch += 1
      queue = []
      chain = Promise.resolve()
      broke = false
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
    failed: () => broke,
  }
}
