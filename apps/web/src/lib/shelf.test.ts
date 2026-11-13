import { describe, expect, it } from 'vitest'
import { clock, readableDocument, sameAnswer, shuffled } from './shelf'

describe('지식 서가 helpers', () => {
  it('reads time codes as a clock', () => {
    expect(clock(130)).toBe('2:10')
    expect(clock(3725)).toBe('1:02:05')
  })

  it('compares short answers loosely but not emptily', () => {
    expect(sameAnswer('더 큰 값', '더 큰 값.')).toBe(true)
    expect(sameAnswer('Vector Clock', 'vector clock')).toBe(true)
    expect(sameAnswer('', '값')).toBe(false)
    expect(sameAnswer('램포트', '벡터 시계')).toBe(false)
  })

  it('lists a document’s sources under it for the citation buttons', () => {
    const text = readableDocument({
      language: 'ko', partial: [], strict: true,
      sources: [{ n: 1, fileId: 'f', name: '03강.pdf', kind: 'file', chars: 10, read: 10 }],
      sections: [{ id: 's', heading: '개요', content: '전역 시계가 없다 [1].' }],
    })
    expect(text).toContain('## 개요')
    expect(text.endsWith('### 출처\n- [1] 03강.pdf')).toBe(true)
  })

  it('shuffles without losing items', () => {
    expect(shuffled([1, 2, 3, 4]).sort()).toEqual([1, 2, 3, 4])
  })
})
