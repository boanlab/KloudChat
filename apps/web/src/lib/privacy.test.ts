import { describe, expect, it } from 'vitest'
import { requestProtection, searchConsentLabel } from './privacy'
import type { PrivacyRouting, SearchConsent } from '@/types'

const base: PrivacyRouting = {
  requestedModels: ['strict-local/writer'], routedModels: ['strict-local/writer'],
  effectiveModels: ['strict-local/writer'], actualModels: [], action: 'none', dataBoundary: 'self_hosted',
}
const consent = (status: SearchConsent['status'], queries: string[] = [], searches?: number): SearchConsent =>
  ({ status, queries, ...(searches !== undefined ? { searches } : {}) })

describe('request protection badge', () => {
  it('says strict-local for every strict-local action, the internal-data move included', () => {
    for (const action of ['strict_local', 'route_strict_local', 'internal_strict_local'] as const) {
      expect(requestProtection({ ...base, action })?.base).toBe('strict-local로 보호됨')
    }
    // a strict-local turn whose tool output was masked afterwards stays strict-local
    expect(requestProtection({ ...base, initialAction: 'internal_strict_local', action: 'strict_local' })?.base)
      .toBe('strict-local로 보호됨')
  })

  it('never claims the request went out unless it did', () => {
    expect(requestProtection({ ...base, action: 'none' })).toBeNull()
    expect(requestProtection({ ...base, action: 'send_raw_external' })?.base).toBe('확인 후 외부 원문 전송')
    expect(requestProtection({ ...base, initialAction: 'send_raw_external', action: 'mask_external' })?.base)
      .toBe('확인 후 요청 원문은 외부 전송')
    expect(requestProtection({ ...base, action: 'mask_external' })?.base).toBe('개인정보를 가려 전송함')
  })

  it('adds what the strict-local turn did about the web', () => {
    const allowed = requestProtection({ ...base, action: 'internal_strict_local',
      searchConsent: consent('allowed', ['연합학습 BN'], 1) })
    expect([allowed?.base, allowed?.search, allowed?.n]).toEqual(['strict-local로 보호됨', '외부 검색 {n}회(허용)', 1])
    for (const status of ['declined', 'blocked', 'unavailable', 'none'] as const) {
      expect(searchConsentLabel(consent(status))).toBe('외부 검색 안 함')
    }
    expect(searchConsentLabel(consent('pending', ['q']))).toBe('외부 검색 확인 대기')
    // allowed but nothing ran: not counted as a search
    expect(searchConsentLabel(consent('allowed', [], 0))).toBe('외부 검색 안 함')
    // a document's routing may carry only the search record
    expect(requestProtection({ searchConsent: consent('auto', ['a', 'b'], 2) })?.base).toBe('strict-local로 보호됨')
  })
})
