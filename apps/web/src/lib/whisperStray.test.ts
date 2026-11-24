import { describe, expect, it } from 'vitest'
import { isStray, wavSeconds } from './whisperStray'

describe('isStray', () => {
  it('drops a stock phrase from a short clip', () => {
    expect(isStray('감사합니다.', 1.2)).toBe(true)
    expect(isStray('시청해 주셔서 감사합니다!', 2)).toBe(true)
    expect(isStray('Thank you.', 1)).toBe(true)
  })

  it('keeps the same words when the clip is long enough to be a real sentence', () => {
    expect(isStray('감사합니다', 4)).toBe(false)
  })

  it('keeps anything else', () => {
    expect(isStray('감사합니다 그런데 하나 더 물어볼게요', 2)).toBe(false)
    expect(isStray('클라우드 컴퓨팅이 뭐야', 1.5)).toBe(false)
  })

  it('reads seconds from a 16 kHz WAV', () => {
    expect(wavSeconds(44 + 32_000 * 2)).toBe(2)
  })
})
