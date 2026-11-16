import { describe, expect, it } from 'vitest'
import { previewSentence } from './previewText'

describe('previewSentence', () => {
  it('drops a table and keeps the prose before it', () => {
    expect(previewSentence('| 시간 | 핵심 주장 | |---|---| | 1교시 | 개요 |')).toBe('')
    expect(previewSentence('요약입니다. 표는 아래. | a | b | |---|---| | 1 | 2 |')).toBe('요약입니다.')
  })
  it('skips the app prompts', () => {
    expect(previewSentence('이 질문에 답하려면 외부 검색이 필요합니다. 아래 검색어만 밖으로 나갑니다.')).toBe('')
  })
  it('strips markup and keeps one sentence', () => {
    expect(previewSentence('## **핵심** 정리를 마쳤습니다. 더 보시려면 [1] 옆을 보세요.')).toBe('핵심 정리를 마쳤습니다.')
  })
})
