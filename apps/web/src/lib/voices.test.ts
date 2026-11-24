import { describe, expect, it } from 'vitest'
import { QWEN_VOICES, VOICES, VOICE_TONE, voiceFor, voicesOf } from './voices'

describe('voices', () => {
  it('describes every voice the picker offers, and the other OpenAI-style ones', () => {
    for (const v of VOICES) expect(VOICE_TONE[v]).toBeTruthy()
    for (const v of ['ash', 'ballad', 'coral', 'sage', 'verse']) expect(VOICE_TONE[v]).toBeTruthy()
  })

  it('describes the nine Qwen3-TTS voices, sohee first', () => {
    expect(QWEN_VOICES).toHaveLength(9)
    expect(QWEN_VOICES[0]).toBe('sohee')
    for (const v of QWEN_VOICES) expect(VOICE_TONE[v]).toBeTruthy()
  })

  it('offers only the selected model voices and never sends one it lacks', () => {
    const qwen = { voices: [...QWEN_VOICES] }
    expect(voicesOf(qwen)).toEqual(QWEN_VOICES)
    expect(voicesOf({})).toEqual(VOICES)
    expect(voicesOf(undefined)).toEqual(VOICES)
    expect(voiceFor(qwen, 'alloy')).toBe('sohee')
    expect(voiceFor(qwen, 'ryan')).toBe('ryan')
    expect(voiceFor(undefined, 'sohee')).toBe('alloy')
    expect(voiceFor({}, 'nova')).toBe('nova')
  })
})
