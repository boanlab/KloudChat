import { describe, expect, it } from 'vitest'
import { translateCount } from './i18n'

describe('translateCount', () => {
  it('says one in the singular and many in the plural, in English', () => {
    expect(translateCount('en', '{n}회 사용', 1)).toBe('1 use')
    expect(translateCount('en', '{n}회 사용', 3)).toBe('3 uses')
    expect(translateCount('en', '메모리 {n}개', 1)).toBe('1 memory')
    expect(translateCount('en', '{n}명', 1)).toBe('1 person')
    expect(translateCount('en', '{n}개', 1)).toBe('1 file')
    expect(translateCount('en', '{n}회 사용', 1200)).toBe('1,200 uses')
  })
  it('says slides for a deck, not images', () => {
    expect(translateCount('en', '{n}장', 12, 'deck')).toBe('12 slides')
    expect(translateCount('en', '{n}장', 1, 'deck')).toBe('1 slide')
    expect(translateCount('en', '{n}장', 3)).toBe('3 images')
    expect(translateCount('ko', '{n}장', 12, 'deck')).toBe('12장')
  })
  it('leaves Korean as it was', () => {
    expect(translateCount('ko', '{n}회 사용', 1)).toBe('1회 사용')
  })
})
