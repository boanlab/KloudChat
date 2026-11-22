import { describe, expect, it } from 'vitest'
import { splitSentences } from './sentences'

describe('splitSentences', () => {
  it('splits after a sentence end and keeps each sentence whole', () => {
    expect(splitSentences('닫혔거나 주소가 올바르지 않습니다. 구성원에게만 열린 챗봇이면 로그인해야 합니다.')).toEqual([
      '닫혔거나 주소가 올바르지 않습니다.',
      '구성원에게만 열린 챗봇이면 로그인해야 합니다.',
    ])
  })

  it('does not split inside a number or without a following space', () => {
    expect(splitSentences('월 1.5배 늘었습니다')).toEqual(['월 1.5배 늘었습니다'])
  })

  it('returns one sentence for a single sentence and nothing for an empty string', () => {
    expect(splitSentences('다시 시도하세요.')).toEqual(['다시 시도하세요.'])
    expect(splitSentences('')).toEqual([])
  })
})
