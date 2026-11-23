import { describe, expect, it } from 'vitest'
import { VOICES, VOICE_TONE } from './voices'

describe('voices', () => {
  it('describes every voice the picker offers, and the other OpenAI-style ones', () => {
    for (const v of VOICES) expect(VOICE_TONE[v]).toBeTruthy()
    for (const v of ['ash', 'ballad', 'coral', 'sage', 'verse']) expect(VOICE_TONE[v]).toBeTruthy()
  })
})
