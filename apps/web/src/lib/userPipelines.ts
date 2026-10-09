import type { Agent, UserPipeline } from '@/types'

export const REVIEW_LABEL: Record<UserPipeline['review'], string> = {
  '': '비공개',
  pending: '승인 대기',
  approved: '공유됨',
  rejected: '반려됨',
}

export const REVIEW_TONE = { '': 'neutral', pending: 'warn', approved: 'success', rejected: 'danger' } as const

/** What an agent makes as a step: a document, an answer, or nothing a step can run. */
export function agentOutput(agent: Agent): '' | 'document' | 'answer' {
  const surface = agent.surface ?? agent.kinds[0] ?? 'chat'
  return surface === 'report' ? 'document' : surface === 'chat' ? 'answer' : ''
}

/** A flow made with 「워크플로 만들기」 and left without a single edit: nothing worth keeping. */
export const isUntouchedPipeline = (p: UserPipeline): boolean =>
  p.version === 1 &&
  p.stages.length === 0 &&
  !p.base &&
  !p.originId &&
  !p.review &&
  !p.description &&
  p.boundAgents.length === 0
