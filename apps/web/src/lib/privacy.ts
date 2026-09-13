import type { PrivacyRouting } from '@/types'

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
