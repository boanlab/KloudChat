import { describe, expect, it } from 'vitest'
import { phoneModelParts } from './phoneModel'

const t = (s: string) => s
const m = (name: string, dataBoundary: string, strictLocal = false) =>
  ({ name, label: name, dataBoundary, strictLocal }) as Parameters<typeof phoneModelParts>[0]

describe('phoneModelParts', () => {
  it('keeps the family when the rest is only a version', () => {
    expect(phoneModelParts(m('Veo 3.1 Lite', 'external'), t)).toEqual({ core: 'Veo 3.1 Lite', place: '외부' })
  })
  it('still drops a family name in front of a model size', () => {
    expect(phoneModelParts(m('Qwen3.8 27b', 'self_hosted'), t)).toEqual({ core: '27b', place: '로컬' })
  })
})
