/**
 * Words for published bots (공개 챗봇): what the publish check found, what a bot leaves out,
 * who it is open to and how its review stands, why it is paused, and the notice a visitor
 * reads before the first question. Also the owner's and the administrator's badge.
 */

import { useEffect } from 'react'
import { create } from 'zustand'
import { ApiError, BASE_URL, publicationsApi } from '@/lib/api'
import type { BotChannel, BotLimits, BotVisibility, Publication, PublicationSettings, PublicationStatus, ShowProcess } from '@/lib/api'
import { useStore } from '@/store/useStore'

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

/** Who a bot is open to, narrowest first. */
export const VISIBILITIES: BotVisibility[] = ['internal', 'link', 'external']

export const VISIBILITY_LABEL: Record<BotVisibility, string> = {
  internal: '구성원 공개',
  link: '링크 공개',
  external: '외부 공개',
}

export const VISIBILITY_HINT: Record<BotVisibility, string> = {
  internal: '이 기관 구성원이 로그인해서 씁니다. 챗봇 목록에 보입니다. 바로 열립니다.',
  link: '주소를 아는 사람은 누구나 로그인 없이 씁니다. 어떤 목록에도 나오지 않습니다. 관리자가 승인하면 열립니다.',
  external: '누구나 로그인 없이 씁니다. 공개 챗봇 목록에 나오고 검색에도 잡힐 수 있습니다. 관리자가 승인하면 열립니다.',
}

/** The owner's view of a request: what is waiting, or why it was refused. */
export function reviewLine(pub: Publication, t: (s: string) => string): string {
  const asked = pub.requestedVisibility ? t(VISIBILITY_LABEL[pub.requestedVisibility]) : ''
  if (pub.reviewState === 'pending') {
    if (asked && pub.pendingVersionId) return t('{scope}와 새 판을 관리자가 확인하고 있습니다.').replace('{scope}', asked)
    if (asked) return t('{scope}를 관리자가 확인하고 있습니다. 그동안 지금 범위로 답합니다.').replace('{scope}', asked)
    if (pub.requestedChannels && !pub.pendingVersionId) return t('채널 요청을 관리자가 확인하고 있습니다. 그동안 지금 열린 채널만 씁니다.')
    return t('새 판을 관리자가 확인하고 있습니다. 그동안 승인된 판이 답합니다.')
  }
  if (pub.reviewState === 'rejected') {
    const what = asked ? t('{scope} 요청이 반려되었습니다.').replace('{scope}', asked) : t('새 판이 반려되었습니다.')
    return pub.reviewNote ? `${what} ${t('사유: {reason}').replace('{reason}', pub.reviewNote)}` : what
  }
  return ''
}

/** The notice before the first question; with 「대화 열람」 on it asks for consent. A visitor
 *  without an account also learns the conversation lives in this browser only. */
export function noticeLines(view: 'off' | 'on', days: number, t: (s: string) => string, signedIn = true): string[] {
  const lines =
    view === 'on'
      ? [
          t('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다.'),
          t('이 챗봇을 만든 사람이 대화 내용을 볼 수 있습니다. {n}일 뒤 지워집니다.').replace('{n}', String(days)),
        ]
      : [t('입력 내용은 이 챗봇을 만든 사람 계정으로 처리·기록됩니다(개선을 위해 개인정보를 가린 통계로만 보입니다).')]
  if (!signedIn) {
    if (view !== 'on') lines.push(t('대화는 마지막 질문 뒤 {n}일이 지나면 지워집니다.').replace('{n}', String(days)))
    lines.push(t('로그인하지 않으면 이 브라우저에서만 대화를 이어 볼 수 있습니다. 쿠키를 지우면 대화를 다시 열 수 없습니다.'))
  }
  lines.push(t('개인정보를 입력하지 마세요.'))
  return lines
}

/** A refused question, in words a visitor can act on. */
export function botErrorMessage(err: unknown, t: (s: string) => string, maxChars?: number): string {
  const code = err instanceof ApiError ? err.detail : ''
  switch (code) {
    case 'visitor_rate_limited':
      return t('질문을 너무 자주 보냈습니다. 잠시 뒤 다시 보내 주세요.')
    case 'visitor_daily_limit':
      return t('오늘 보낼 수 있는 질문을 다 보냈습니다. 내일 다시 이용해 주세요.')
    case 'daily_credits_reached':
      return t('오늘 이 챗봇이 쓸 수 있는 양을 다 썼습니다. 내일 다시 이용해 주세요.')
    case 'visitor_cookie_required':
      return t('브라우저가 쿠키를 막고 있어 대화를 시작할 수 없습니다. 쿠키를 허용한 뒤 다시 열어 주세요.')
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

/* ── channels ─────────────────────────────────────────────────────────── */

/** Where a bot reaches beyond its own page, each opened by an administrator. */
export const CHANNELS: BotChannel[] = ['embed', 'api', 'mcp']

export const CHANNEL_LABEL: Record<BotChannel, string> = {
  embed: '다른 사이트에 넣기',
  api: 'API',
  mcp: 'MCP',
}

export const CHANNEL_HINT: Record<BotChannel, string> = {
  embed: '기관 홈페이지 같은 내 사이트에 대화창을 넣습니다. 외부 공개일 때 쓸 수 있고, 넣을 사이트 주소도 관리자가 승인합니다.',
  api: '내 서비스나 프로그램에서 OpenAI 호환 방식으로 이 챗봇을 부릅니다. 링크·외부 공개일 때 쓸 수 있습니다.',
  mcp: 'Claude 같은 AI 도구에 이 챗봇을 도구로 연결합니다. 링크·외부 공개일 때 쓸 수 있습니다.',
}

export const CHANNEL_ERROR: Record<string, string> = {
  bad_origin: '사이트 주소는 https://로 시작하는 주소만 쓸 수 있습니다. 경로 없이 적어 주세요.',
  origins_required: '넣을 사이트 주소를 하나 이상 적어 주세요.',
  channel_needs_audience: '이 채널은 지금 공개 범위에서 쓸 수 없습니다. 공개 범위를 먼저 넓혀 주세요.',
  credits_cap_required: '채널을 열려면 하루 최대 크레딧을 정해야 합니다.',
  revoked: '닫힌 챗봇입니다.',
}

/** Whether an audience can carry the channel: framing needs everyone, the API and MCP an
 *  audience outside the members. */
export function channelAllowed(visibility: BotVisibility, channel: BotChannel): boolean {
  if (channel === 'embed') return visibility === 'external'
  return visibility !== 'internal'
}

export type ChannelState = 'open' | 'pending' | 'closed' | 'unavailable'

/** One channel as the owner reads it. */
export function channelState(pub: Publication, channel: BotChannel): ChannelState {
  if (pub.channels?.[channel]) return 'open'
  if (pub.reviewState === 'pending' && pub.requestedChannels?.[channel]) return 'pending'
  return channelAllowed(pub.requestedVisibility || pub.visibility, channel) ? 'closed' : 'unavailable'
}

/** The API's address as other programs reach it. */
export function apiBase(): string {
  return new URL(BASE_URL, window.location.origin).href.replace(/\/$/, '')
}

export function mcpUrl(slug: string): string {
  return `${apiBase()}/bots/mcp/${slug}`
}

/** The line a site pastes to frame the bot. */
export function embedSnippet(slug: string): string {
  return `<script src="${window.location.origin}/embed/widget.js" data-bot="${slug}" async></script>`
}

/** The owner's refusals on the audience, in words. */
export const SCOPE_ERROR: Record<string, string> = {
  credits_cap_required: '구성원 밖으로 공개하려면 하루 최대 크레딧을 정해야 합니다.',
  revoked: '닫힌 챗봇입니다.',
}

interface BotAttention {
  /** The caller's bots with something new to look at. */
  bots: number
  /** For an administrator: requests waiting for review. */
  reviews: number
  refresh: () => Promise<void>
}

export const useBotAttention = create<BotAttention>((set) => ({
  bots: 0,
  reviews: 0,
  refresh: async () => {
    try {
      set(await publicationsApi.attention())
    } catch {
      /* the badge is a convenience; a failed read leaves it as it was */
    }
  },
}))

/** Reads the bot badges on sign-in and every few minutes after. Mounted once, in the shell. */
export function useBotAttentionBadge() {
  const refresh = useBotAttention((s) => s.refresh)
  const userId = useStore((s) => s.user?.id)
  useEffect(() => {
    if (!userId) return
    void refresh()
    const timer = setInterval(() => void refresh(), 5 * 60_000)
    return () => clearInterval(timer)
  }, [userId, refresh])
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

/** The largest daily credit cap the server stores (`publishing.CREDITS_PER_DAY_MAX`). */
export const CREDITS_PER_DAY_MAX = 100_000_000

/** A daily credit cap as typed: a whole number from 1 up to the largest, or undefined. */
export function creditsCap(raw: string | number | undefined): number | undefined {
  if (typeof raw === 'string' && !/^\s*\d+\s*$/.test(raw)) return undefined
  const value = Number(raw)
  return Number.isInteger(value) && value >= 1 && value <= CREDITS_PER_DAY_MAX ? value : undefined
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
