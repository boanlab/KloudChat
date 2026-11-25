import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./speakProblems', async (orig) => ({ ...(await orig<typeof import('./speakProblems')>()) }))
vi.mock('./api', () => ({
  ApiError: class ApiError extends Error {
    status: number
    detail: string
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
      this.detail = detail
    }
  },
}))

import { ApiError } from './api'
import { createVoicePlayer } from './voicePlayer'

class FakeAudio {
  static all: FakeAudio[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = true
  ended = false
  src: string
  constructor(src: string) {
    this.src = src
    FakeAudio.all.push(this)
  }
  play() {
    this.paused = false
    return Promise.resolve()
  }
  pause() {
    this.paused = true
  }
  finish() {
    this.ended = true
    this.onended?.()
  }
}

const block = (text: string) => ({ kind: 'para' as const, source: '', text })
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
async function until(check: () => boolean) {
  for (let i = 0; i < 50 && !check(); i += 1) await tick()
  expect(check()).toBe(true)
}

beforeEach(() => {
  FakeAudio.all = []
  vi.stubGlobal('Audio', FakeAudio)
  vi.stubGlobal('URL', { createObjectURL: () => 'blob:x', revokeObjectURL: () => undefined })
})

describe('the voice conversation player', () => {
  it('skips a block that fails and plays the next', async () => {
    const synth = vi.fn(async (b: { text: string }) => {
      if (b.text === 'bad') throw new ApiError(502, 'down')
      return new Blob(['x'])
    })
    const player = createVoicePlayer(synth, () => undefined)
    player.add(block('one'))
    player.add(block('bad'))
    player.add(block('three'))
    await until(() => FakeAudio.all.length === 1)
    FakeAudio.all[0].finish()
    await until(() => FakeAudio.all.length === 2)
    expect(player.failed()).toBe(false)
  })

  it('stops the answer after three misses in a row, saying why', async () => {
    const player = createVoicePlayer(async () => { throw new ApiError(502, 'down') }, () => undefined)
    for (const text of ['a', 'b', 'c']) player.add(block(text))
    await until(() => player.failed())
    expect(player.problem()).toBe('failed')
  })

  it('stops at once for a refusal every block would meet', async () => {
    const player = createVoicePlayer(async () => { throw new ApiError(422, 'insufficient_credits') }, () => undefined)
    player.add(block('a'))
    await until(() => player.failed())
    expect(player.problem()).toBe('credits')
  })

  it('says so when the browser will not play, instead of going on silently', async () => {
    class Refusing extends FakeAudio {
      play() {
        return Promise.reject(new DOMException('no', 'NotAllowedError'))
      }
    }
    vi.stubGlobal('Audio', Refusing)
    const player = createVoicePlayer(async () => new Blob(['x']), () => undefined)
    player.add(block('a'))
    await until(() => player.failed())
    expect(player.problem()).toBe('playback')
  })

  it('aborts what the speech server is still making when stopped', async () => {
    let signal: AbortSignal | undefined
    const player = createVoicePlayer(
      (_b, s) => {
        signal = s
        return new Promise(() => undefined)
      },
      () => undefined,
    )
    player.add(block('a'))
    await until(() => signal !== undefined)
    player.stop()
    expect(signal?.aborted).toBe(true)
    expect(player.failed()).toBe(false)
  })
})
