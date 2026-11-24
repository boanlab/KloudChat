import { describe, expect, it } from 'vitest'
import { Vad } from './vad'

const FRAME = 128

/** A small repeatable random source, so the noise in a test is the same every run. */
function random(seed = 7) {
  let s = seed
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296
    return s / 4294967296
  }
}

/** Frame loudness of steady room noise: around `level`, wandering a little. */
const room = (rand: () => number, level: number) => () => level * (0.7 + 0.6 * rand())
/** A slowly drifting noise (the engine of a car), which stays within ±25% of `level`. */
const drift = (rand: () => number, level: number) => {
  let x = 0
  return () => {
    x = x * 0.9 + (rand() - 0.5) * 0.3
    return level * (1 + Math.max(-0.25, Math.min(0.25, x)))
  }
}
/** Talk: words of about 300 ms with 120 ms between them, `level` above the room. */
const talk = (rand: () => number, level: number, room: number) => {
  let t = 0
  return () => {
    t += FRAME
    const inWord = t % 420 < 300
    return (inWord ? level * (0.6 + 0.8 * rand()) : room * (0.7 + 0.6 * rand())) as number
  }
}

const run = (vad: Vad, source: () => number, ms: number, bar = 1) => {
  const events: string[] = []
  for (let t = 0; t < ms; t += FRAME) {
    const verdict = vad.feed(source(), FRAME, bar)
    if (verdict) events.push(verdict)
  }
  return events
}

describe('Vad in a quiet room', () => {
  it('starts on ordinary laptop-microphone speech and ends after the pause', () => {
    const rand = random()
    const vad = new Vad()
    expect(run(vad, room(rand, 0.003), 1000)).toEqual([])
    expect(run(vad, talk(rand, 0.03, 0.003), 2500)).toEqual(['start'])
    expect(run(vad, room(rand, 0.003), 1500)).toEqual(['end'])
    expect(vad.noisy).toBe(false)
  })

  it('does not take a keyboard click for speech', () => {
    const vad = new Vad()
    expect(run(vad, () => 0.05, 100)).toEqual([])
    expect(run(vad, () => 0.003, 600)).toEqual([])
  })

  it('wants louder speech while the app is playing sound', () => {
    const vad = new Vad()
    expect(run(vad, () => 0.015, 600, 2.5)).toEqual([])
    expect(run(vad, () => 0.06, 600, 2.5)).toEqual(['start'])
  })
})

describe('Vad in a noisy room', () => {
  for (const [name, level, make] of [
    ['a café (white noise)', 0.03, room],
    ['a car (drifting noise)', 0.04, drift],
  ] as const) {
    it(`gives up on ${name} within seconds and then hears speech over it`, () => {
      const rand = random(11)
      const vad = new Vad()
      const source = make(rand, level)
      // The noise is louder than the starting bar: it is cut as steady sound, and dropped.
      const first = run(vad, source, 9000)
      expect(first).toEqual(['start', 'end'])
      expect(vad.steady).toBe(true)
      expect(vad.noisy).toBe(true)
      // Now the room is the background: no more false starts.
      expect(run(vad, source, 10_000)).toEqual([])
      // A person talking over it is heard, and is not dropped.
      expect(run(vad, talk(rand, level * 5, level), 3000)).toEqual(['start'])
      expect(run(vad, source, 2000)).toEqual(['end'])
      expect(vad.noisy).toBe(false)
    })
  }

  it('does not hear a voice that is not above the room', () => {
    const rand = random(3)
    const vad = new Vad()
    run(vad, room(rand, 0.03), 9000)
    run(vad, room(rand, 0.03), 4000)
    expect(run(vad, talk(rand, 0.045, 0.03), 4000)).toEqual([])
  })

  it('a long talk with pauses is cut at the longest span but is not noise', () => {
    const rand = random(5)
    const vad = new Vad({ maxMs: 5000, endMs: 5000, steadyMs: 3000 })
    run(vad, room(rand, 0.003), 400)
    const events = run(vad, talk(rand, 0.05, 0.003), 8000)
    expect(events).toContain('start')
    expect(events).toContain('end')
    expect(vad.steady).toBe(false)
  })

  it('a faint stretch that crossed the bar once is dropped, not sent to Whisper', () => {
    const rand = random(9)
    const vad = new Vad()
    run(vad, room(rand, 0.005), 3000)
    // A blip of 250 ms above the bar, then the room again: a voice would have been longer.
    let calls = 0
    const bump = () => (calls++ < 2 ? 0.02 : 0.005)
    const events = run(vad, bump, 2500)
    expect(events).toEqual(['start', 'end'])
    expect(vad.noisy).toBe(true)
  })

  it('a loud room cannot raise the bar past what speech can clear', () => {
    const vad = new Vad({ maxMs: 3000, steadyMs: 3000 })
    run(vad, () => 0.5, 8000)
    expect(vad.threshold()).toBeLessThanOrEqual(0.05 * 3.5 + 1e-9)
  })
})
