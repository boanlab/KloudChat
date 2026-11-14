import { describe, expect, it } from 'vitest'
import { reasoningLine, reasoningSteerable } from './reasoning'
import type { ModelInfo } from '@/types'

const t = (s: string) => s

describe('reasoningLine', () => {
  it('names the chip and the level the writer was sent', () => {
    expect(reasoningLine({ mode: 'deep', effort: 'high' }, t)).toBe('깊게 · high')
    expect(reasoningLine({ mode: 'fast', effort: 'none' }, t)).toBe('빠르게 · 추론 끔')
    expect(reasoningLine({ mode: 'deep', effort: 'on' }, t)).toBe('깊게 · 추론 켬')
    expect(reasoningLine({ mode: 'auto', effort: 'default' }, t)).toBe('자동 · 모델 기본')
  })
  it('says so when the model could not do what the chip asked', () => {
    expect(reasoningLine({ mode: 'deep', effort: 'default', unsupported: true }, t)).toBe(
      '깊게 · 이 모델은 조절 불가',
    )
  })
})

describe('reasoningSteerable', () => {
  const plain = { id: 'x/plain', reasoningControl: false } as ModelInfo
  const steerable = { id: 'x/think', reasoningControl: true } as ModelInfo
  it('is always on under Super Agent, which picks the model', () => {
    expect(reasoningSteerable('super', plain)).toBe(true)
    expect(reasoningSteerable('super_strict', undefined)).toBe(true)
  })
  it('follows the picked model otherwise', () => {
    expect(reasoningSteerable('manual', steerable)).toBe(true)
    expect(reasoningSteerable('manual', plain)).toBe(false)
    expect(reasoningSteerable('manual', undefined)).toBe(false)
  })
})
