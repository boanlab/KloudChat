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
 * Mode a new conversation starts in (mirrors the server's `start_mode.starting_mode`): the person's pick, else the admin's
 * start for the surface; an agent's own model wins. An unusable `super` start falls to strict-local Super Agent, then the model;
 * `super_strict` never widens. While the catalogue is `pending` the start is taken as usable.
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
  if (kind !== 'chat' && kind !== 'report' && kind !== 'slides' && kind !== 'sheet') return 'manual'
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

type StartInputs = Parameters<typeof startingMode>[0]

/** Why a new conversation starts in Super Agent (strict-local), when it does. */
export type StrictStartReason = 'project' | 'personal' | 'admin' | 'unavailable'

/** Why `startingMode` lands on `super_strict` (null otherwise): project policy, saved or admin strict-local default, or no usable external entry. */
export function strictStartReason(inputs: StartInputs): StrictStartReason | null {
  if (startingMode(inputs) !== 'super_strict') return null
  const { kind, homePick, defaultRouting, projectId, superAgent } = inputs
  const routed = kind as 'chat' | 'report' | 'slides' | 'sheet'
  const start = homePick && isSuperEntry(homePick) ? homePick : defaultRouting?.[routed] ?? 'super'
  if (start === 'super_strict') return homePick === 'super_strict' ? 'personal' : 'admin'
  if (projectId && superAgent.projectStrict) return 'project'
  return 'unavailable'
}

/** The short line that says why a new conversation starts strict-local (Korean source text). */
export const STRICT_START_HINT: Record<StrictStartReason, string> = {
  project: '기관 정책에 따라 strict-local로 시작합니다',
  personal: '기본 모델 설정(strict-local)에 따라 시작합니다',
  admin: '관리자 기본 설정에 따라 strict-local로 시작합니다',
  unavailable: '쓸 수 있는 외부 모델이 없어 strict-local로 시작합니다',
}

/** 「Super Agent」 or 「Super Agent (strict-local)」: product names, not translated. */
export function superLaneTitle(mode: 'super' | 'super_strict'): string {
  return mode === 'super_strict' ? 'Super Agent (strict-local)' : 'Super Agent'
}

/** Rows that move a turn rather than name a class's own model: empty keeps the class's row. */
export const SUPER_OVERRIDE_ROWS: ReadonlySet<SuperAgentClass> = new Set(['material', 'complex_document', 'document_plan', 'document_hard', 'document_repair'])

/** What a task class reads as under an answer and in the admin table. */
export const SUPER_CLASS_LABEL: Record<SuperAgentClass, string> = {
  simple: '간단한 질문',
  chat: '일반 대화',
  transform: '번역·요약·다듬기',
  calc: '계산',
  code: '코드',
  visual: '대시보드·차트',
  current: '최신 정보 검색',
  long: '긴 자료',
  reasoning: '복잡한 추론',
  document: '보고서·슬라이드',
  material: '내부 자료 작업',
  complex_document: '복잡한 문서',
  document_plan: '문서 개요·구성',
  document_hard: '문서의 어려운 부분',
  document_repair: '문서 고쳐 쓰기',
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

/** Why a document counted as complex, as the answer's line says it. */
const COMPLEX_REASON_LABEL: Record<NonNullable<SuperAgentRoute['complexReason']>, string> = {
  form: '양식을 따름',
  coverage: '자료를 모두 다룸',
  tools: '차트·그림 작업',
  long_source: '긴 자료',
}

/** 「이번 답: Qwen3.8 Flash · 간단한 질문」 and its reason; `label` is the model that answered (privacy routing may move it). */
export function superRouteLabel(
  route: SuperAgentRoute,
  modelLabel: (id: string) => string,
  t: (s: string) => string,
): { label: string; title: string } {
  const answered = modelLabel(route.fallbackModel ?? route.boundaryModel ?? route.selectedModel)
  const complex = route.row === 'complex_document'
  const why = [t(SUPER_CLASS_LABEL[complex ? 'complex_document' : route.taskClass] ?? route.taskClass)]
  if (complex && route.complexReason) why.push(t(COMPLEX_REASON_LABEL[route.complexReason] ?? route.complexReason))
  if (route.row === 'material') why.push(t('내부 자료라 자료에 강한 모델'))
  if (route.source === 'escalated') why.push(t('더 강한 설정으로 다시'))
  else if (route.source === 'sticky') why.push(t('앞 답과 같은 모델'))
  else if (route.source === 'deescalated') why.push(t('짧은 맺음말'))
  if (route.reasoningMode === 'fast') why.push(t('빠르게 고름'))
  else if (route.reasoningMode === 'deep') why.push(t('깊게 고름'))
  if (route.strictReason === 'personal_data') why.push(t('개인정보 감지로 내부 모델'))
  else if (route.strictReason === 'internal_data') why.push(t('내부 자료라 내부 모델'))
  if (route.fallbackModel) {
    why.push(
      t(route.fallbackReason === 'queue' ? '{model} 모델 차례가 길어 대신 씀' : '{model} 모델이 응답하지 않아 이어 씀').replace(
        '{model}',
        modelLabel(route.selectedModel),
      ),
    )
  } else if (route.skipped) {
    why.push(t('{model} 모델은 최근 응답하지 않아 건너뜀').replace('{model}', modelLabel(route.skipped)))
  }
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
