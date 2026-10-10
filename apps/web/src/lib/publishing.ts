/**
 * Words for published bots (공개 챗봇): what the publish check found, what a bot leaves out,
 * why it is paused, and the notice a visitor reads before the first question.
 */

import { ApiError } from '@/lib/api'
import type { BotLimits, PublicationSettings, PublicationStatus, ShowProcess } from '@/lib/api'

/** Why an agent cannot be published as it stands. */
export const PROBLEM_LABEL: Record<string, string> = {
  no_local_model: '이 기관에서 쓸 수 있는 내부 모델이 없어 공개할 수 없습니다. 관리자에게 문의하세요.',
  not_chat: '대화로 답하는 에이전트만 챗봇으로 공개할 수 있습니다.',
  sealed: '봉인된 사본은 공개할 수 없습니다.',
  own_process: '직접 구성한 진행 과정을 쓰는 에이전트는 챗봇으로 공개할 수 없습니다.',
  credits: '남은 크레딧이 없어 공개할 수 없습니다.',
  account: '계정이 활성 상태가 아니어서 공개할 수 없습니다.',
  retired: '더 이상 제공하지 않는 에이전트입니다.',
  not_owner: '내 에이전트만 공개할 수 있습니다.',
}

/** What a published bot does not use, whatever the agent has. */
export const DISABLED_LABEL: Record<string, string> = {
  memory: '내 메모리',
  personal: '개인 맞춤 설정',
  files: '내 다른 파일·프로젝트',
  connectors: '커넥터',
  web: '웹 검색',
  documents: '문서·발표 만들기',
  tools: '자료 찾기·계산 외 도구',
  skills: '스킬',
  questions: '쓰기 전에 묻는 질문',
  own_checks: '직접 만든 검사 스킬',
}

export const FINDING_LABEL: Record<string, string> = {
  phone: '휴대전화 번호',
  email: '개인 이메일',
  government_id: '주민등록번호',
  payment_card: '카드 번호',
  ip_address: 'IP 주소',
}

export const STATUS_LABEL: Record<PublicationStatus, string> = {
  draft: '준비 중',
  pending: '승인 대기',
  approved: '공개 중',
  paused: '일시 중지',
  rejected: '반려',
  revoked: '닫힘',
}

export const PAUSE_LABEL: Record<string, string> = {
  owner: '만든 사람이 멈췄습니다.',
  admin: '관리자가 멈췄습니다.',
  credits: '만든 사람의 크레딧이 다 떨어져 멈췄습니다. 크레딧이 다시 채워지면 열립니다.',
  account: '만든 사람의 계정을 쓸 수 없어 멈췄습니다.',
  model: '답할 내부 모델을 쓸 수 없어 멈췄습니다. 새 판으로 다시 공개하세요.',
}

export const SHOW_PROCESS_LABEL: Record<ShowProcess, string> = {
  none: '보여 주지 않음',
  summary: '단계 이름만',
  full: '자세히',
}

export const RETENTION_DAYS = [7, 14, 30, 90, 180]

/** The notice before the first question; with 「대화 열람」 on it asks for consent. */
export function noticeLines(view: 'off' | 'on', days: number, t: (s: string) => string): string[] {
  if (view === 'on') {
    return [
      t('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다.'),
      t('이 챗봇을 만든 사람이 대화 내용을 볼 수 있습니다. {n}일 뒤 지워집니다.').replace('{n}', String(days)),
      t('개인정보를 입력하지 마세요.'),
    ]
  }
  return [
    t('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다(개선을 위해 개인정보를 가린 통계로만 보입니다).'),
    t('개인정보를 입력하지 마세요.'),
  ]
}

/** A refused question, in words a visitor can act on. */
export function botErrorMessage(err: unknown, t: (s: string) => string, maxChars?: number): string {
  const code = err instanceof ApiError ? err.detail : ''
  switch (code) {
    case 'visitor_rate_limited':
      return t('질문을 너무 자주 보냈습니다. 잠시 뒤 다시 보내 주세요.')
    case 'bot_rate_limited':
    case 'bot_busy':
      return t('지금 이 챗봇에 질문이 몰려 있습니다. 잠시 뒤 다시 보내 주세요.')
    case 'daily_turns_reached':
    case 'daily_tokens_reached':
      return t('오늘 이 챗봇이 답할 수 있는 양을 다 썼습니다. 내일 다시 이용해 주세요.')
    case 'conversation_full':
      return t('이 대화가 길어졌습니다. 새 대화를 시작해 주세요.')
    case 'too_long':
      return t('질문이 너무 깁니다. {n}자 안으로 줄여 주세요.').replace('{n}', String(maxChars ?? ''))
    case 'bot_paused':
      return t('지금은 이 챗봇을 쓸 수 없습니다.')
    case 'consent_required':
      return t('안내를 다시 확인해 주세요.')
    case 'published_model_unavailable':
      return t('지금은 답할 내부 모델을 쓸 수 없습니다. 잠시 뒤 다시 시도해 주세요.')
    case 'visitor_blocked':
      return t('이 챗봇을 쓸 수 없습니다.')
    default:
      return t('답을 받지 못했습니다. 다시 시도해 주세요.')
  }
}

/** The bot's address, for copying. */
export function botUrl(slug: string): string {
  return `${window.location.origin}/b/${slug}`
}

/** The settings a bot's owner chooses, in the publish dialog and on the dashboard. */
export interface SettingsDraft {
  title: string
  description: string
  greeting: string
  prompts: string
  placeholder: string
  showProcess: ShowProcess
  showSources: boolean
  retentionDays: number
  conversationView: 'off' | 'on'
  limits: BotLimits
}

export function toSettings(draft: SettingsDraft): PublicationSettings {
  return {
    title: draft.title.trim() || undefined,
    description: draft.description.trim(),
    branding: {
      greeting: draft.greeting.trim(),
      suggestedPrompts: draft.prompts.split('\n').map((p) => p.trim()).filter(Boolean).slice(0, 4),
      placeholder: draft.placeholder.trim(),
    },
    showProcess: draft.showProcess,
    showSources: draft.showSources,
    retentionDays: draft.retentionDays,
    conversationView: draft.conversationView,
    limits: draft.limits,
  }
}
