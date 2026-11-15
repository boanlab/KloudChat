import { createHash } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { diagramKey } from './diagramKey'
import { randomId, sha256 } from './cryptoFallback'

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')

describe('crypto stand-ins for plain-http origins', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('sha256 matches the standard at every padding boundary', () => {
    for (const length of [0, 1, 55, 56, 63, 64, 65, 119, 120, 1000]) {
      const data = new Uint8Array(length).map((_, i) => (i * 31 + 7) & 0xff)
      expect(hex(sha256(data))).toBe(createHash('sha256').update(data).digest('hex'))
    }
    const korean = new TextEncoder().encode('flowchart TD\n  A[요청 받기] --> B[회의록 정리]')
    expect(hex(sha256(korean))).toBe(createHash('sha256').update(korean).digest('hex'))
  })

  it('a diagram key is the same with and without crypto.subtle', async () => {
    const source = 'mindmap\n  root((클라우드))  \n    가상화\n'
    const withSubtle = await diagramKey(source)
    vi.stubGlobal('crypto', { getRandomValues: (a: Uint8Array<ArrayBuffer>) => a })
    const without = await diagramKey(source)
    expect(without).toBe(withSubtle)
    expect(without).toMatch(/^[0-9a-f]{16}$/)
  })

  it('randomId gives a v4 UUID without crypto.randomUUID', () => {
    const real = globalThis.crypto
    vi.stubGlobal('crypto', { getRandomValues: (a: Uint8Array<ArrayBuffer>) => real.getRandomValues(a) })
    const id = randomId()
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/)
    expect(randomId()).not.toBe(id)
  })
})
