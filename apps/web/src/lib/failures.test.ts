import { describe, expect, it } from 'vitest'
import { refusalSentence } from '@/lib/failures'

const t = (text: string) => text

describe('refusal sentences', () => {
  it('does not call a server failure a refusal', () => {
    // A bare 500 reads `http_500`; the caller's own fallback names the failure.
    expect(refusalSentence('http_500', t)).toBeUndefined()
    expect(refusalSentence('http_503', t)).toBeUndefined()
  })

  it('still names an unknown 4xx code', () => {
    expect(refusalSentence('http_409', t)).toBe('요청이 거부되었습니다 (http_409).')
  })
})
