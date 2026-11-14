import type { PrivacyRouting, SearchConsent } from '@/types'

/** Interface labels for the privacy detector's categories, shared by the composer dialog and the transcript. */
export const FINDING_LABEL: Record<string, string> = {
  email: '이메일',
  phone: '전화번호',
  government_id: '주민 식별번호',
  payment_card: '결제카드',
  ip_address: 'IP 주소',
  api_key: 'API 키',
  jwt: 'JWT',
  private_key: '개인키',
}

const CONTEXT_SOURCES = new Set([
  'conversation_history', 'attachments', 'user_instructions', 'project_instructions',
  'project_design', 'project_knowledge', 'memory', 'agent', 'skills', 'tool_definitions',
])

export function requestMaskingLabel(findings: PrivacyRouting['findingCounts']): string {
  const sources = (findings ?? []).filter((finding) =>
    finding.count > 0 && !['tool_output', 'assistant_output'].includes(finding.source),
  ).map((finding) => finding.source)
  if (!sources.length || sources.some((source) => source !== 'current_input' && !CONTEXT_SOURCES.has(source))) {
    return '개인정보를 가려 전송함'
  }
  const input = sources.includes('current_input')
  const context = sources.some((source) => CONTEXT_SOURCES.has(source))
  return input && context ? '요청·참고자료를 가려 전송함'
    : input ? '사용자 입력을 가려 전송함' : '참고자료를 가려 전송함'
}

/** Only aggregate labels reach the tooltip, never original values or unknown metadata strings. */
export function maskingCategories(findings: PrivacyRouting['findingCounts']) {
  const counts = new Map<string, number>()
  for (const finding of findings ?? []) {
    if (!Number.isSafeInteger(finding.count) || finding.count <= 0) continue
    const label = Object.hasOwn(FINDING_LABEL, finding.category)
      ? FINDING_LABEL[finding.category] : '기타 민감정보'
    counts.set(label, (counts.get(label) ?? 0) + finding.count)
  }
  return [...counts].map(([label, count]) => ({ label, count }))
}

/** A message's search-consent record (`routing.searchConsent`), when it has one. */
export function searchCardOf(message: { routing?: unknown }): SearchConsent | undefined {
  const routing = message.routing
  if (!routing || typeof routing !== 'object' || !('searchConsent' in routing)) return undefined
  const found = (routing as { searchConsent?: SearchConsent }).searchConsent
  return found && typeof found === 'object' ? found : undefined
}

const STRICT_ACTIONS = new Set(['strict_local', 'route_strict_local', 'internal_strict_local'])

/**
 * The request-protection badge, read from what the routing record says happened:
 * `base` names how the request was protected, `search` what a strict-local turn did about the
 * web (`n` searches). Untranslated; null when the record claims nothing.
 */
export function requestProtection(routing: Partial<PrivacyRouting> | null | undefined): {
  base: string
  search: string | null
  n: number
  tone: 'success' | 'warn'
} | null {
  if (!routing) return null
  const consent = routing.searchConsent
  const requested = routing.initialAction ?? routing.action
  const search = consent ? searchConsentLabel(consent) : null
  const n = consent ? searchCount(consent) : 0
  // A search-consent record exists only on strict-local turns.
  if ((requested && STRICT_ACTIONS.has(requested)) || consent) {
    return { base: 'strict-local로 보호됨', search, n, tone: 'success' }
  }
  if (requested === 'mask_external') {
    return { base: requestMaskingLabel(routing.findingCounts), search: null, n: 0, tone: 'success' }
  }
  if (requested === 'send_raw_external') {
    // Raw request sent; tool output may have been masked afterwards.
    return {
      base: routing.action === 'send_raw_external' ? '확인 후 외부 원문 전송' : '확인 후 요청 원문은 외부 전송',
      search: null,
      n: 0,
      tone: 'warn',
    }
  }
  return null
}

/** Searches a strict-local turn actually sent. */
export function searchCount(consent: SearchConsent): number {
  if (consent.status !== 'allowed' && consent.status !== 'auto') return 0
  return consent.searches ?? consent.queries.length
}

/** What the turn did about the web, untranslated (`{n}` filled by the caller). */
export function searchConsentLabel(consent: SearchConsent): string {
  if (consent.status === 'pending') return '외부 검색 확인 대기'
  return searchCount(consent) > 0 ? '외부 검색 {n}회(허용)' : '외부 검색 안 함'
}
