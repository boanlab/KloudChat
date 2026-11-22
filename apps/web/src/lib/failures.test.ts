import { beforeAll, describe, expect, it, vi } from 'vitest'
import { ApiError } from '@/lib/api'
import { mediaFailure, refusalSentence, uploadFailure } from '@/lib/failures'
import { translator } from '@/lib/i18n'

const t = translator(() => 'ko')

describe('refusal sentences', () => {
  it('does not call a server failure a refusal', () => {
    // A bare 500 reads `http_500`; the caller's own fallback names the failure.
    expect(refusalSentence('http_500', t)).toBeUndefined()
    expect(refusalSentence('http_503', t)).toBeUndefined()
  })

  it('never shows an unknown code, only what to do', () => {
    for (const code of ['http_409', 'something_new_from_server']) {
      const sentence = refusalSentence(code, t)
      expect(sentence).toBe('요청을 처리하지 못했습니다. 다시 해도 같으면 관리자에게 문의하세요.')
      expect(sentence).not.toContain(code)
    }
  })

  it('has a Korean sentence for every code the routers raise', () => {
    const codes = [
      'agent_retired', 'retry_not_latest', 'retry_target_not_found', 'search_consent_stale', 'session_id_taken',
      'no_audio_models', 'diagram_failed', 'fork_chat_only', 'fork_target_not_found', 'fork_user_message_required',
      'handoff_from_chat_only', 'auto_is_not_a_model_id', 'auto_routing_chat_only',
    ]
    for (const code of codes) {
      const sentence = refusalSentence(code, t)
      expect(sentence, code).toMatch(/[가-힣]/)
      expect(sentence, code).not.toContain(code)
      expect(sentence, code).not.toContain('요청을 처리하지 못했습니다. 다시 해도')
    }
  })

  it('explains a 5xx code of ours in the media path', () => {
    expect(mediaFailure(new ApiError(502, 'diagram_failed'), 'fallback', t)).toBe('도식을 그리지 못했습니다. 잠시 뒤 다시 시도하세요.')
    expect(mediaFailure(new ApiError(502, 'http_502'), 'fallback', t)).toBe('fallback')
  })
})

describe('upload failures', () => {
  // The too-large sentence reads the page language; the unit run has no page.
  beforeAll(() => { vi.stubGlobal('document', { documentElement: { lang: 'ko' } }) })
  const fail = (status: number, detail: string) => uploadFailure(new ApiError(status, detail), t)

  it('names the limit for a file that is too large and does not advise a retry', () => {
    const named = fail(413, 'file_too_large_50mb')
    expect(named).toContain('50MB')
    expect(named).not.toContain('다시 시도')
    expect(fail(413, 'http_413')).toMatch(/너무 큽니다/)
  })

  it('tells an unsupported or unreadable file apart from a server failure', () => {
    expect(fail(415, 'not_media')).toMatch(/지원하지 않는 형식/)
    expect(fail(422, 'http_422')).toMatch(/비어 있거나/)
    expect(fail(500, 'http_500')).toMatch(/서버 문제.*다시 시도/)
    expect(fail(502, 'http_502')).toMatch(/서버 문제/)
  })

  it('says to check the connection when no answer came', () => {
    expect(uploadFailure(new TypeError('Failed to fetch'), t)).toMatch(/인터넷 연결/)
    expect(fail(408, 'timeout')).toMatch(/인터넷 연결/)
  })

  it('keeps the explained too-long video and an expired sign-in distinct', () => {
    expect(fail(413, 'video_too_long_30min')).toContain('30분')
    expect(fail(401, 'token_expired')).toMatch(/로그인/)
  })
})
