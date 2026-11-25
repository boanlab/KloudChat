import { beforeEach, describe, expect, it, vi } from 'vitest'

const speak = vi.hoisted(() => vi.fn())
// the silent clip that unlocks sound is not one of the reading's clips
vi.mock('./speakProblems', async (orig) => ({ ...(await orig<typeof import('./speakProblems')>()), unlockAudio: () => undefined }))
vi.mock('./api', () => ({
  speakApi: { speak },
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
import { pauseReading, readPieces, readState, resumeReading, startReading, stopReading } from './readAloud'

/** A clip that plays until the test ends it. */
class FakeAudio {
  static all: FakeAudio[] = []
  onended: (() => void) | null = null
  onerror: (() => void) | null = null
  paused = true
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
    this.paused = true
    this.onended?.()
  }
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
async function until(check: () => boolean) {
  for (let i = 0; i < 50 && !check(); i += 1) await tick()
  expect(check()).toBe(true)
}

const text = (n: number) => Array.from({ length: n }, (_, i) => `${i + 1}번째 문단입니다.`).join('\n\n')
const piecesOf = (n: number) => readPieces([{ text: text(n), label: '본문' }]).pieces

beforeEach(() => {
  stopReading()
  FakeAudio.all = []
  speak.mockReset()
  speak.mockImplementation(async () => new Blob(['x'], { type: 'audio/wav' }))
  vi.stubGlobal('Audio', FakeAudio)
})

describe('the blocks of a text', () => {
  it('cuts a text into paragraphs, a whole list and a whole table', () => {
    const { pieces, cut } = readPieces([
      { heading: '개요', text: '첫 문단입니다.\n\n- 하나\n- 둘\n\n| 항목 | 값 |\n| --- | --- |\n| 규모 | 2억 |' },
    ])
    expect(cut).toBe(false)
    expect(pieces.map((p) => p.block.kind)).toEqual(['para', 'list', 'table'])
    expect(pieces[0].block.source).toContain('개요')
  })

  it('says a code block is on the screen instead of reading it', () => {
    const { pieces } = readPieces([{ text: '```python\nprint(1)\n```' }])
    expect(pieces).toHaveLength(1)
    expect(pieces[0].block).toMatchObject({ kind: 'code', source: '' })
  })

  it('stops at the limit and says the text was cut', () => {
    const { pieces, cut } = readPieces([{ text: text(30) }], 200)
    expect(cut).toBe(true)
    expect(pieces.length).toBeGreaterThan(0)
    expect(pieces.length).toBeLessThan(30)
  })

  it('carries each unit’s label and place', () => {
    const { pieces } = readPieces([
      { label: '1장', at: 'a', text: '가 문단입니다.' },
      { label: '2장', at: 'b', text: '나 문단입니다.' },
    ])
    expect(pieces.map((p) => [p.label, p.at])).toEqual([['1장', 'a'], ['2장', 'b']])
  })
})

describe('the reading', () => {
  it('goes block by block, shows progress, and ends idle', async () => {
    const onAt = vi.fn()
    const onEnd = vi.fn()
    startReading('answer:1', piecesOf(3), { voice: 'sohee', onAt, onEnd })
    expect(readState()).toMatchObject({ key: 'answer:1', status: 'loading', total: 3, index: 0 })

    await until(() => FakeAudio.all.length === 1)
    expect(readState()).toMatchObject({ status: 'playing', index: 0 })
    FakeAudio.all[0].finish()
    await until(() => FakeAudio.all.length === 2)
    expect(readState().index).toBe(1)
    FakeAudio.all[1].finish()
    await until(() => FakeAudio.all.length === 3)
    FakeAudio.all[2].finish()
    await until(() => readState().status === 'idle')
    expect(onAt).toHaveBeenCalledTimes(3)
    expect(onEnd).toHaveBeenCalledTimes(1)
  })

  it('sends each block with its spoken-words source and the chosen voice', async () => {
    startReading('k', readPieces([{ text: '- 하나\n- 둘' }]).pieces, { voice: 'vivian' })
    await until(() => speak.mock.calls.length === 1)
    const [, voice, opts] = speak.mock.calls[0]
    expect(voice).toBe('vivian')
    expect(opts).toMatchObject({ spoken: true })
    expect(opts.source).toContain('- 하나')
  })

  it('voices only a couple of blocks ahead of the one playing', async () => {
    startReading('k', piecesOf(8), { voice: 'sohee' })
    await until(() => FakeAudio.all.length === 1)
    expect(speak).toHaveBeenCalledTimes(3)
    FakeAudio.all[0].finish()
    await until(() => FakeAudio.all.length === 2)
    expect(speak).toHaveBeenCalledTimes(4)
  })

  it('stops at once: the sound ends, nothing more is voiced, the end is told', async () => {
    const onEnd = vi.fn()
    startReading('k', piecesOf(5), { voice: 'sohee', onEnd })
    await until(() => FakeAudio.all.length === 1)
    stopReading()
    expect(FakeAudio.all[0].paused).toBe(true)
    expect(readState().status).toBe('idle')
    expect(onEnd).toHaveBeenCalledTimes(1)
    const calls = speak.mock.calls.length
    await tick()
    await tick()
    expect(speak.mock.calls.length).toBe(calls)
    expect(FakeAudio.all).toHaveLength(1)
  })

  it('a second reading takes over from the first', async () => {
    const first = vi.fn()
    startReading('a', piecesOf(3), { voice: 'sohee', onEnd: first })
    await until(() => FakeAudio.all.length === 1)
    startReading('b', piecesOf(2), { voice: 'sohee' })
    expect(first).toHaveBeenCalledTimes(1)
    expect(readState()).toMatchObject({ key: 'b', index: 0, total: 2 })
  })

  it('pauses and resumes the clip that is playing', async () => {
    startReading('k', piecesOf(2), { voice: 'sohee' })
    await until(() => FakeAudio.all.length === 1)
    pauseReading()
    expect(readState().status).toBe('paused')
    expect(FakeAudio.all[0].paused).toBe(true)
    resumeReading()
    expect(readState().status).toBe('playing')
    expect(FakeAudio.all[0].paused).toBe(false)
  })

  it('gives up with a busy note when the speech server says too many', async () => {
    speak.mockRejectedValue(new ApiError(429, 'busy'))
    startReading('k', piecesOf(2), { voice: 'sohee' })
    await until(() => readState().problem === 'limit')
    expect(readState()).toMatchObject({ key: 'k', status: 'idle' })
  })

  it('gives up quietly with a failure note after three misses in a row', async () => {
    speak.mockRejectedValue(new ApiError(502, 'down'))
    startReading('k', piecesOf(4), { voice: 'sohee' })
    await until(() => readState().problem === 'failed')
    expect(readState()).toMatchObject({ key: 'k', status: 'idle' })
  })

  it('skips one block that fails and reads the rest', async () => {
    let n = 0
    speak.mockImplementation(async () => {
      n += 1
      if (n === 2) throw new ApiError(502, 'down')
      return new Blob(['x'], { type: 'audio/wav' })
    })
    startReading('k', piecesOf(3), { voice: 'sohee' })
    await until(() => FakeAudio.all.length === 1)
    FakeAudio.all[0].finish()
    await until(() => FakeAudio.all.length === 2)
    expect(readState().problem).toBe('')
    FakeAudio.all[1].finish()
    await until(() => readState().status === 'idle')
    expect(FakeAudio.all.length).toBe(2)
  })

  it('stops for a refusal the rest would meet too (no credits, no model)', async () => {
    speak.mockRejectedValue(new ApiError(422, 'insufficient_credits'))
    startReading('k', piecesOf(3), { voice: 'sohee' })
    await until(() => readState().problem === 'credits')
    speak.mockReset()
    speak.mockRejectedValue(new ApiError(503, 'no_speech_model'))
    startReading('k', piecesOf(3), { voice: 'sohee' })
    await until(() => readState().problem === 'unavailable')
  })

  it('does not go on when the browser refuses to play', async () => {
    class Refusing extends FakeAudio {
      play() {
        return Promise.reject(new DOMException('no', 'NotAllowedError'))
      }
    }
    vi.stubGlobal('Audio', Refusing)
    startReading('k', piecesOf(3), { voice: 'sohee' })
    await until(() => readState().problem === 'playback')
  })

  it('asks the speech server to stop what it is still making when the reading is stopped', async () => {
    let signal: AbortSignal | undefined
    speak.mockImplementation((_t, _v, opts: { signal?: AbortSignal }) => {
      signal = opts.signal
      return new Promise(() => undefined)
    })
    startReading('k', piecesOf(2), { voice: 'sohee' })
    await until(() => signal !== undefined)
    stopReading()
    expect(signal?.aborted).toBe(true)
  })

  it('does nothing for a text with nothing to read', () => {
    startReading('k', [], { voice: 'sohee' })
    expect(readState().status).toBe('idle')
  })
})
