/** Audit log screen words: action, field and value labels. Every `ACTIONS` code needs a label (`tests/test_audit_log.py`). */
import { kindMeta } from '@/lib/kinds'
import type { AuditRow } from '@/lib/api'
import type { SessionKind } from '@/types'

export const ACTION_LABEL: Record<string, string> = {
  signup: '회원가입',
  'signup.verified': '이메일 인증 완료',
  'signup.verification_resent': '인증 메일 재발송',
  login: '로그인',
  logout: '로그아웃',
  'password.change': '비밀번호 변경',
  'password.reset_requested': '비밀번호 재설정 요청',
  'password.reset': '비밀번호 재설정',
  'session.revoke': '로그인 세션 종료',
  'token.reuse': '토큰 재사용 감지',
  'user.approve': '가입 승인',
  'user.reject': '가입 반려',
  'user.suspend': '계정 정지',
  'user.reinstate': '정지 해제',
  'user.role': '권한 변경',
  'user.update': '계정 정보 수정',
  'user.password_reset': '비밀번호 초기화',
  'user.litellm_key': 'LiteLLM 키 교체',
  'user.delete': '계정 삭제',
  'user.models': '모델 허용 변경',
  'credits.set': '크레딧 변경',
  'key.create': 'API 키 발급',
  'key.revoke': 'API 키 폐기',
  'settings.update': '시스템 설정 변경',
  'governance.update': '정책 변경',
  'branding.logo': '로고 변경',
  'branding.logo.clear': '로고 삭제',
  'pipeline.approve': '진행 과정 승인',
  'connector.catalog.save': '커넥터 카탈로그 저장',
  'connector.catalog.delete': '커넥터 카탈로그 삭제',
  'pipeline.reject': '진행 과정 반려',
  'issue.update': '문제 신고 처리 변경',
  'issue.snapshot_view': '신고된 대화 열람',
  'skill.import': '스킬 업로드',
  'skill.approve': '스킬 공유 승인',
  'skill.reject': '스킬 공유 반려',
  'storage.reclaim': '저장 공간 자동 정리',
  'storage.reclaim.manual': '저장 공간 정리',
  'filter.blocked': '의도 필터 차단',
  'pii.masked': '개인정보 가림',
  'routing.auto': 'Auto 라우팅',
  'privacy.none': '개인정보 검사 통과',
  'privacy.decision_required': '개인정보 처리 선택 요청',
  'privacy.internal_strict_local': '내부 자료 strict-local 처리',
  'privacy.internal_data_requires_strict_local': '내부 자료 전송 거절',
  'privacy.strict_local': 'strict-local 모델로 전환',
  'privacy.route_strict_local': 'strict-local 모델로 전환 (사용자 선택)',
  'privacy.mask_external': '가려서 외부 전송',
  'privacy.send_raw_external': '원문 외부 전송',
  'privacy.mask_tool_output': '도구 결과 가림',
  'privacy.strict_tool_output': '도구 결과 strict-local 처리',
  'model.fallback': '대체 모델이 답함',
  'publication.published': '챗봇 공개',
  'publication.requested': '챗봇 공개 범위 승인 요청',
  'publication.request_withdrawn': '챗봇 승인 요청 취소',
  'publication.reviewed': '챗봇 공개 검토',
  'publication.narrowed': '챗봇 공개 범위 좁힘',
  'publication.withdrawn': '챗봇 링크·외부 공개 닫기',
  'publication.version': '챗봇 새 판 공개',
  'publication.rollback': '챗봇 판 되돌리기',
  'publication.settings': '챗봇 설정 변경',
  'publication.paused': '챗봇 일시 중지',
  'publication.resumed': '챗봇 다시 열기',
  'publication.admin_status': '관리자의 챗봇 상태 변경',
  'publication.deleted': '챗봇 삭제',
  'publication.channels_requested': '챗봇 채널 요청',
  'publication.channels_closed': '챗봇 채널 닫기',
  'publication.key_created': '챗봇 키 발급',
  'publication.key_revoked': '챗봇 키 폐기',
  'publication.consent': '챗봇 이용 안내 동의',
  'publication.shared': '방문자의 대화 보내기',
  'publication.conversation_read': '만든 사람의 챗봇 대화 열람',
  'publication.admin_list': '관리자의 챗봇 대화 목록 열람',
  'publication.admin_read': '관리자의 챗봇 대화 열람',
  'publication.visitor_blocked': '챗봇 방문자 차단',
  'publication.visitor_unblocked': '챗봇 방문자 차단 해제',
  'conversation.delete': '사용자 대화 삭제',
  'conversation.bulk_delete': '사용자 대화 일괄 삭제',
  'project.delete': '사용자 프로젝트 삭제',
  'project.bulk_delete': '사용자 프로젝트 일괄 삭제',
  'file.delete': '사용자 파일 삭제',
  'knowledge.delete': '사용자 에이전트 지식 파일 삭제',
  'artifact.delete': '사용자 아티팩트 삭제',
  'artifact.bulk_delete': '사용자 아티팩트 일괄 삭제',
  'memory.delete': '사용자 메모리 삭제',
  'agent.delete': '사용자 에이전트 삭제',
  'agent.bulk_delete': '사용자 에이전트 일괄 삭제',
  'skill.delete': '사용자 스킬 삭제',
  'skill.bulk_delete': '사용자 스킬 일괄 삭제',
  'design.delete': '사용자 테마 삭제',
  'design.bulk_delete': '사용자 테마 일괄 삭제',
  'template.delete': '사용자 템플릿 삭제',
  'pipeline.delete': '사용자 진행 과정 삭제',
}

/** A person deleting their own content; one row per request, counts in metadata. */
const DELETION_CODES = [
  'conversation.delete',
  'conversation.bulk_delete',
  'project.delete',
  'project.bulk_delete',
  'file.delete',
  'knowledge.delete',
  'artifact.delete',
  'artifact.bulk_delete',
  'memory.delete',
  'agent.delete',
  'agent.bulk_delete',
  'skill.delete',
  'skill.bulk_delete',
  'design.delete',
  'design.bulk_delete',
  'template.delete',
  'pipeline.delete',
]
const DELETION_ACTIONS = new Set(DELETION_CODES)

/** A sign-in row's outcome lives in its detail. */
const LOGIN_DETAIL: Record<string, string> = {
  failed: '로그인 실패',
  locked: '로그인 잠김',
  refresh_reuse: '토큰 재사용 감지',
}

export function actionLabel(row: Pick<AuditRow, 'action' | 'detail'>): string {
  if (row.action === 'login' && LOGIN_DETAIL[row.detail]) return LOGIN_DETAIL[row.detail]
  return ACTION_LABEL[row.action] ?? row.action
}

/** Filter groups for 「행위 종류」: label and the codes sent as `action`. */
export const ACTION_GROUPS: { id: string; label: string; codes: string }[] = [
  { id: 'auth', label: '로그인 · 계정 보안', codes: 'login,signup,signup.*,password.*,session.*,token.*' },
  { id: 'users', label: '사용자 관리', codes: 'user.*,credits.*' },
  { id: 'settings', label: '시스템 설정 · 정책', codes: 'settings.*,governance.*,branding.*' },
  { id: 'keys', label: 'API 키', codes: 'key.*' },
  { id: 'pipelines', label: '진행 과정 검토', codes: 'pipeline.*' },
  { id: 'skills', label: '스킬 검토', codes: 'skill.approve,skill.reject' },
  { id: 'issues', label: '문제 신고', codes: 'issue.*' },
  { id: 'privacy', label: '개인정보 · 라우팅', codes: 'privacy.*,pii.*,filter.*,routing.*' },
  { id: 'storage', label: '저장 공간', codes: 'storage.*' },
  { id: 'publications', label: '챗봇 공개 · 채널', codes: 'publication.*' },
  { id: 'deletions', label: '사용자 콘텐츠 삭제', codes: DELETION_CODES.join(',') },
]

/** Recorded field names (governance, system settings, accounts) to their screen labels. */
export const FIELD_LABEL: Record<string, string> = {
  // governance
  pii_masking: '개인정보 마스킹',
  external_data_guard: '외부 모델 개인정보 보호',
  allow_user_raw_external: '사용자 원문 외부 전송 허용',
  privacy_safe_model_ids: 'strict-local 안전 모델',
  adaptive_routing_enabled: 'Auto 비용 절약 라우팅',
  adaptive_classifier_model_id: '난이도 분류 모델',
  adaptive_economy_model_ids: '절약 모델 우선순위',
  adaptive_quality_enabled: 'Auto 품질 우선 라우팅',
  adaptive_quality_model_ids: '상향 모델 우선순위',
  internal_data_strict_local: '내부 자료는 strict-local로만',
  outline_model_id: '구성 단계 모델',
  intent_filter: '의도 기반 필터',
  blocked_categories: '차단할 주제',
  retention_days: '대화 보존 기간',
  idle_timeout_minutes: '유휴 자동 로그아웃',
  // system settings
  baseUrl: 'LiteLLM 주소',
  masterKey: '마스터 키',
  brand_name: '서비스 이름',
  contact_email: '문의 주소',
  enabled_kinds: '사용할 기능',
  backend_base_url: '기능 서버 주소',
  tools_search_url: '웹 검색 주소',
  tools_fetch_url: '문서 가져오기 주소',
  tools_exec_url: '코드 실행 주소',
  tools_research_url: '심층 조사 주소',
  tools_stt_url: '음성 전사 주소',
  tools_index_url: '자료 검색 주소',
  smtp_host: 'SMTP 서버',
  smtp_port: 'SMTP 포트',
  smtp_security: 'SMTP 보안',
  smtp_username: 'SMTP 사용자 이름',
  smtp_password: 'SMTP 비밀번호',
  smtp_from: '보내는 주소',
  app_base_url: '서비스 주소',
  signup_mode: '가입 방식',
  signup_domains: '가입 가능한 이메일 도메인',
  signup_verify_email: '이메일 인증',
  model_chat: '대화 기본 모델',
  model_report: '보고서 기본 모델',
  model_slides: '발표 자료 기본 모델',
  model_image: '그림 기본 모델',
  model_audio: '오디오 기본 모델',
  model_video: '동영상 기본 모델',
  model_title: '제목·기억·요약 모델',
  model_code: '코드 작성 모델',
  model_code_fallback: '코드 작성 대체 모델',
  thinking_self_hosted: '자체 호스팅 모델 생각',
  thinking_external: '외부 모델 생각',
  default_monthly_credits: '새 계정 월 크레딧',
  // accounts and reviews
  status: '상태',
  role: '권한',
  monthly_credits: '월 크레딧',
  allowed_models: '허용 모델',
  name: '이름',
  email: '이메일',
  review: '검토 상태',
  holdReason: '보류 사유',
  assignee: '담당자',
}

/** Settings screen section of each system setting; mirrors `_SETTING_SECTION` in admin.py. */
const SETTING_SECTION: Record<string, string> = {
  baseUrl: 'proxy',
  masterKey: 'proxy',
  brand_name: 'branding',
  contact_email: 'branding',
  enabled_kinds: 'features',
  backend_base_url: 'tools',
  tools_search_url: 'tools',
  tools_fetch_url: 'tools',
  tools_exec_url: 'tools',
  tools_research_url: 'tools',
  tools_stt_url: 'tools',
  tools_index_url: 'tools',
  smtp_host: 'mail',
  smtp_port: 'mail',
  smtp_security: 'mail',
  smtp_username: 'mail',
  smtp_password: 'mail',
  smtp_from: 'mail',
  app_base_url: 'mail',
  signup_mode: 'signup',
  signup_domains: 'signup',
  signup_verify_email: 'signup',
  model_chat: 'models',
  model_report: 'models',
  model_slides: 'models',
  model_image: 'models',
  model_audio: 'models',
  model_video: 'models',
  model_title: 'models',
  model_code: 'models',
  model_code_fallback: 'models',
  thinking_self_hosted: 'models',
  thinking_external: 'models',
  default_monthly_credits: 'signup',
}

const SECTION_LABEL: Record<string, string> = {
  proxy: 'LiteLLM 프록시',
  branding: '브랜드',
  features: '기능',
  tools: '기능 서버',
  mail: '메일 발송',
  signup: '가입',
  models: '모델',
  other: '기타',
}

/** Fields a row changed: from its recorded diff, else its comma-list detail (older rows). */
export function changedFields(row: AuditRow): string[] {
  const changes = row.metadata?.changes
  if (changes && typeof changes === 'object') return Object.keys(changes)
  if (row.action !== 'settings.update' && row.action !== 'governance.update') return []
  return row.detail
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
}

/** Row target as words; settings changes name their sections. */
export function targetParts(row: AuditRow): string[] {
  if (row.action === 'settings.update') {
    const recorded = row.metadata?.sections
    const sections = Array.isArray(recorded)
      ? (recorded as string[])
      : [...new Set(changedFields(row).map((f) => SETTING_SECTION[f] ?? 'other'))]
    return ['시스템 설정', ...sections.map((s) => SECTION_LABEL[s] ?? s)]
  }
  return row.target ? [row.target] : []
}

/** Words for a `key=value` detail on older rows, and for the values some actions write. */
const DETAIL_KEY: Record<string, string> = {
  credits: '크레딧',
  monthly: '월 크레딧',
  role: '권한',
  status: '상태',
  sessions_revoked: '끝낸 로그인 세션',
  others: '다른 세션 종료',
}
const DETAIL_WORD: Record<string, string> = {
  replaced: '기존 키로 교체',
  rotated: '새 키 발급',
  failed: '',
  locked: '',
  refresh_reuse: '',
}

/** Counts a person's own delete records, in the order the server writes its detail. */
const DELETED_COUNT: [string, string][] = [
  ['sessions', '대화 {n}개'],
  ['messages', '메시지 {n}개'],
  ['artifacts', '아티팩트 {n}개'],
  ['projects', '프로젝트 {n}개'],
  ['files', '파일 {n}개'],
  ['memories', '메모리 {n}개'],
  ['agents', '에이전트 {n}개'],
  ['skills', '스킬 {n}개'],
  ['designs', '테마 {n}개'],
  ['templates', '템플릿 {n}개'],
  ['pipelines', '진행 과정 {n}개'],
]

/** A delete row's counts from its metadata, so the words follow the screen language. */
function deletionWords(meta: Record<string, unknown>, t: (s: string) => string): string {
  const parts = meta.all === true ? [t('전체 삭제')] : []
  for (const [key, words] of DELETED_COUNT) {
    if (typeof meta[key] === 'number') parts.push(t(words).replace('{n}', (meta[key] as number).toLocaleString()))
  }
  if (typeof meta.sessions === 'number' && typeof meta.files === 'number' && meta.files > 0) {
    parts.push(t('첨부 파일은 지우지 않음'))
  }
  return parts.join(' · ')
}

/** Free-text detail made readable, or '' when the label already says it. */
export function detailWords(row: AuditRow, t: (s: string) => string): string {
  const detail = row.detail.trim()
  if (row.metadata && DELETION_ACTIONS.has(row.action)) {
    return deletionWords(row.metadata, t) || detail
  }
  // A chatbot row names the bot; an administrator's reading or closing also says why.
  const reason = typeof row.metadata?.reason === 'string' ? row.metadata.reason.trim() : ''
  if (row.action.startsWith('publication.') && reason) {
    return [detail, t('사유: {reason}').replace('{reason}', reason)].filter(Boolean).join(' · ')
  }
  if (!detail) return ''
  if (detail in DETAIL_WORD) return DETAIL_WORD[detail] ? t(DETAIL_WORD[detail]) : ''
  if (changedFields(row).length) return ''
  const pairs = detail.split(',').map((part) => part.trim())
  if (pairs.every((part) => /^[a-z_]+=/.test(part))) {
    return pairs
      .map((part) => {
        const [key, value] = part.split('=', 2)
        return `${t(DETAIL_KEY[key] ?? key)} ${t(ENUM_VALUE[value] ?? value)}`
      })
      .join(' · ')
  }
  return detail
}

const ENUM_VALUE: Record<string, string> = {
  active: '사용 중',
  pending: '승인 대기',
  suspended: '정지',
  admin: '관리자',
  user: '사용자',
  approved: '승인',
  rejected: '거절',
  open: '바로 사용',
  approval: '승인 후 사용',
  closed: '받지 않음',
  starttls: 'STARTTLS',
  ssl: 'SSL',
  none: '없음',
  received: '접수',
  checking: '확인 중',
  resolved: '해결',
  on_hold: '보류',
}

const MODEL_FIELDS = new Set([
  'privacy_safe_model_ids',
  'adaptive_classifier_model_id',
  'adaptive_economy_model_ids',
  'adaptive_quality_model_ids',
  'outline_model_id',
  'allowed_models',
])

/** One recorded value as the screen says it; model ids become their display names. */
export function valueWords(
  field: string,
  value: unknown,
  t: (s: string) => string,
  modelName: (id: string) => string,
): string {
  if (value === null || value === undefined || value === '') {
    return field === 'allowed_models' ? t('전체 모델') : t('(없음)')
  }
  if (typeof value === 'boolean') return value ? t('켬') : t('끔')
  if (Array.isArray(value)) {
    if (!value.length) return field === 'allowed_models' ? t('전체 모델') : t('(없음)')
    return value.map((v) => valueWords(field, v, t, modelName)).join(', ')
  }
  if (typeof value === 'number') {
    if (field === 'retention_days') return value ? t('{n}일').replace('{n}', String(value)) : t('계속 보관')
    if (field === 'idle_timeout_minutes') return value ? t('{n}분').replace('{n}', String(value)) : t('사용 안 함')
    return value.toLocaleString()
  }
  const text = String(value)
  if (MODEL_FIELDS.has(field)) return modelName(text)
  if (field === 'signup_verify_email') return ['1', 'true', 'on', 'yes'].includes(text.toLowerCase()) ? t('켬') : t('끔')
  if (field === 'enabled_kinds') {
    const kinds = text.split(',').filter(Boolean)
    if (!kinds.length) return t('챗만')
    return kinds.map((k) => t(kindMeta[k as SessionKind]?.label ?? k)).join(', ')
  }
  return ENUM_VALUE[text] ? t(ENUM_VALUE[text]) : text
}

/** Korean words a search could mean, mapped back to codes the server can match. */
export function actionsMatching(query: string, t: (s: string) => string): string[] {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const hit = (label: string) => label.toLowerCase().includes(q) || t(label).toLowerCase().includes(q)
  const codes = Object.entries(ACTION_LABEL)
    .filter(([, label]) => hit(label))
    .map(([code]) => code)
  // 「로그인 실패」 and the like are sign-in rows told apart by their detail.
  if (Object.values(LOGIN_DETAIL).some(hit) && !codes.includes('login')) codes.push('login')
  return codes
}
