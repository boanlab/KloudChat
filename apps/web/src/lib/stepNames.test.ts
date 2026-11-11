import { describe, expect, it } from 'vitest'
import { collapsedStepNames } from './stepNames'

const ko = (text: string) => text

describe('collapsedStepNames', () => {
  it('counts a repeated step once with its number of runs', () => {
    expect(
      collapsedStepNames(['문서 확인', '문서 편집', '문서 확인', '문서 편집'], ko, 3),
    ).toBe('문서 확인 2회 · 문서 편집 2회')
  })

  it('keeps single steps as they were and folds kinds past the limit', () => {
    expect(collapsedStepNames(['웹 검색', '페이지 읽기', '계산', '차트', '계산'], ko, 3)).toBe(
      '웹 검색 · 페이지 읽기 · 계산 2회 · 외 1개',
    )
  })

  it('names in the reader language', () => {
    const en = (text: string) =>
      ({ '문서 확인': 'Check document', '{n}회': '{n} times' })[text] ?? text
    expect(collapsedStepNames(['문서 확인', '문서 확인'], en, 3)).toBe('Check document 2 times')
  })
})
