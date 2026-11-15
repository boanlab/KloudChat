import type { ModelCatalogue } from '@/lib/api'
import type { RoutingMode, SessionKind, SuperAgentClass, SuperAgentRoute } from '@/types'

export function isSuperMode(mode: RoutingMode | undefined): mode is 'super' | 'super_strict' {
  return mode === 'super' || mode === 'super_strict'
}

/** A surface pick that is a Super Agent picker entry rather than a model id. */
export function isSuperEntry(pick: string | null | undefined): pick is 'super' | 'super_strict' {
  return pick === 'super' || pick === 'super_strict'
}

/**
 * The mode a new conversation starts in (the server's `start_mode.starting_mode`): on chat,
 * report and slides, the person's own pick for the surface (`homePick`: a model starts on that
 * model, a Super Agent entry on that entry), else the admin's start for the surface
 * (`defaultRouting`, Super Agent unless a model was fixed there); a conversation whose agent has
 * its own model stays on it. A `super` start whose
 * entry has nothing this person may use falls to Super Agent (strict-local), then to the
 * surface's model; a `super_strict` start never widens to `super`. Before the catalogue answers
 * (`pending`) the start is taken as usable. Existing conversations keep their own mode; the
 * server moves a project conversation that must stay strict-local to `super_strict`.
 */
export function startingMode({
  kind,
  agentModel,
  superAgent,
  homePick,
  defaultRouting,
  projectId,
}: {
  kind: SessionKind
  /** The conversation's agent's own model, if it fixes one. */
  agentModel?: string | null
  superAgent: Pick<NonNullable<ModelCatalogue['superAgent']>, 'default' | 'strict' | 'pending' | 'projectStrict'>
  homePick?: string | null
  defaultRouting?: ModelCatalogue['defaultRoutingByKind']
  /** The project the conversation starts in: internal material keeps a `super` start strict-local. */
  projectId?: string | null
}): RoutingMode {
  if (kind !== 'chat' && kind !== 'report' && kind !== 'slides') return 'manual'
  if (agentModel) return 'manual'
  if (homePick && !isSuperEntry(homePick)) return 'manual'
  const start = homePick && isSuperEntry(homePick) ? homePick : defaultRouting?.[kind] ?? 'super'
  if (start === 'manual') return 'manual'
  if (superAgent.pending) return start
  // the server's rule (`start_mode.usable_super`): a project moves `super` to `super_strict`
  if (start === 'super' && projectId && superAgent.projectStrict && superAgent.strict.available) {
    return 'super_strict'
  }
  if (start === 'super' && superAgent.default.available) return 'super'
  return superAgent.strict.available ? 'super_strict' : 'manual'
}

/** 「Super Agent」 or 「Super Agent (strict-local)」: product names, not translated. */
export function superLaneTitle(mode: 'super' | 'super_strict'): string {
  return mode === 'super_strict' ? 'Super Agent (strict-local)' : 'Super Agent'
}

/** What a task class reads as under an answer and in the admin table. */
export const SUPER_CLASS_LABEL: Record<SuperAgentClass, string> = {
  simple: '간단한 질문',
  chat: '일반 대화',
  transform: '번역·요약·다듬기',
  calc: '계산',
  code: '코드',
  current: '최신 정보 검색',
  long: '긴 자료',
  reasoning: '복잡한 추론',
  document: '보고서·슬라이드',
  escalation: '다시 요청·빈 답 뒤',
}

export const SUPER_CLASSES = Object.keys(SUPER_CLASS_LABEL) as SuperAgentClass[]

export const SUPER_EFFORT_LABEL: Record<SuperAgentRoute['effort'], string> = {
  default: '모델 기본',
  none: '추론 끔',
  low: '추론 낮게',
  high: '추론 높게',
  highest: '추론 최대',
}

/**
 * 「이번 답: Qwen3.8 Flash · 간단한 질문」 and the why behind it (title text).
 * `label` names the model that answered (privacy routing may have moved it).
 */
export function superRouteLabel(
  route: SuperAgentRoute,
  modelLabel: (id: string) => string,
  t: (s: string) => string,
): { label: string; title: string } {
  const answered = modelLabel(route.boundaryModel ?? route.selectedModel)
  const why = [t(SUPER_CLASS_LABEL[route.taskClass] ?? route.taskClass)]
  if (route.source === 'escalated') why.push(t('더 강한 설정으로 다시'))
  else if (route.source === 'sticky') why.push(t('앞 답과 같은 모델'))
  else if (route.source === 'deescalated') why.push(t('짧은 맺음말'))
  if (route.reasoningMode === 'fast') why.push(t('빠르게 고름'))
  else if (route.reasoningMode === 'deep') why.push(t('깊게 고름'))
  if (route.strictReason === 'personal_data') why.push(t('개인정보 감지로 내부 모델'))
  else if (route.strictReason === 'internal_data') why.push(t('내부 자료라 내부 모델'))
  const label = `${t('이번 답')}: ${answered} · ${why.join(' · ')}`
  const details = [
    `${superLaneTitle(route.mode)}`,
    `${t('판정')}: ${route.source === 'classifier' ? t('분류 모델') : t('규칙')}`,
    `${t('추론')}: ${t(SUPER_EFFORT_LABEL[route.effort] ?? route.effort)}`,
    route.tools === 'none' ? t('도구 없이') : null,
    route.classifierModel
      ? `${t('분류')}: ${route.classifierModel} · ${(route.classifierInputTokens ?? 0) + (route.classifierOutputTokens ?? 0)} tokens · ${route.classifierMs ?? 0} ms`
      : null,
  ].filter(Boolean)
  return { label, title: details.join(' · ') }
}
