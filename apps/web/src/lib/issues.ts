/** 「문의·신고」 client: labels, dialog open state, reply badge. */
import { useEffect } from 'react'
import { create } from 'zustand'
import { useStore } from '@/store/useStore'
import { issuesApi, type IssueCut, type IssueKind, type IssueStatus } from '@/lib/api'

export const KIND_LABEL: Record<IssueKind, string> = {
  bug: '오류 신고',
  feature: '기능 요청',
  other: '문의',
}

export const STATUS_LABEL: Record<IssueStatus, string> = {
  received: '접수',
  checking: '확인 중',
  resolved: '해결',
  on_hold: '보류',
}

export const STATUS_TONE: Record<IssueStatus, 'neutral' | 'accent' | 'success' | 'warn'> = {
  received: 'neutral',
  checking: 'accent',
  resolved: 'success',
  on_hold: 'warn',
}

/** What the snapshot cut to fit; `{n}` is the count. */
export const CUT_LABEL: Record<IssueCut['code'], string> = {
  evidence: '단계 근거를 뺐습니다',
  versions: '이전 판 본문을 뺐습니다',
  steps: '도구 실행 기록을 뺐습니다',
  content: '긴 메시지 {n}개를 줄였습니다',
  artifacts: '아티팩트 {n}개의 본문을 뺐습니다',
  messages: '앞쪽 메시지 {n}개를 뺐습니다',
  bodies: '본문을 앞부분만 남겼습니다',
}

export const SCREENSHOT_TYPES = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']
export const MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024
export const MAX_BODY = 4000

/** Server refusals the form says in words. */
export const CREATE_ERROR: Record<string, string> = {
  issue_rate_limited: '잠시 동안 신고를 너무 많이 보냈습니다. 몇 분 뒤 다시 보내 주세요.',
  internal_data_confirm_required: '내부 자료가 포함된 대화입니다. 공유 확인에 체크해 주세요.',
  screenshot_not_image: '스크린샷은 PNG, JPEG, GIF, WebP 그림만 올릴 수 있습니다.',
  screenshot_too_large: '스크린샷은 5MB 이하로 올려 주세요.',
  session_not_found: '이 대화를 찾을 수 없습니다.',
  message_not_found: '고른 답변을 찾을 수 없습니다.',
  artifact_not_found: '고른 아티팩트를 찾을 수 없습니다.',
}

interface IssueTarget {
  sessionId?: string
  messageId?: string
  /** The answer's first words, for the pinned chip. */
  messageSnippet?: string
  artifactId?: string
  artifactTitle?: string
  kind?: IssueKind
}

interface IssueStore {
  open: boolean
  target: IssueTarget
  unread: number
  openReport: (target?: IssueTarget) => void
  close: () => void
  refreshUnread: () => Promise<void>
}

export const useIssues = create<IssueStore>((set) => ({
  open: false,
  target: {},
  unread: 0,
  openReport: (target = {}) => set({ open: true, target }),
  close: () => set({ open: false }),
  refreshUnread: async () => {
    try {
      const { count } = await issuesApi.unread()
      set({ unread: count })
    } catch {
      /* the badge is a convenience; a failed read leaves it as it was */
    }
  },
}))

/** The page's own build: the hashed entry script name, which changes with each deploy. */
function appBuild(): string {
  const script = document.querySelector<HTMLScriptElement>('script[type="module"][src]')
  const src = script?.getAttribute('src') ?? ''
  const name = src.split('/').pop() ?? ''
  return name.slice(0, 120) || 'dev'
}

/** Browser facts sent with a report. No page content. */
export function clientInfo(): Record<string, string> {
  let timezone = ''
  try {
    timezone = Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    timezone = ''
  }
  return {
    userAgent: navigator.userAgent.slice(0, 400),
    appBuild: appBuild(),
    path: window.location.pathname.slice(0, 200),
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    lang: document.documentElement.lang || 'ko',
    timezone,
  }
}

/** Reads the reply badge on sign-in and every few minutes after. Mounted once, in the shell. */
export function useIssueBadge() {
  const refreshUnread = useIssues((s) => s.refreshUnread)
  const userId = useStore((s) => s.user?.id)
  useEffect(() => {
    if (!userId) return
    void refreshUnread()
    const timer = setInterval(() => void refreshUnread(), 5 * 60_000)
    return () => clearInterval(timer)
  }, [userId, refreshUnread])
}
