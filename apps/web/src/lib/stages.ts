/**
 * Pipeline stage records as the person reads them: one status per row, the waiting card's
 * place, and an agent's pipeline as a short chain of steps.
 */
import type { Message, PipelineDefinition, PipelineStage, Session } from '@/types'

/** What a row's icon says: ✓ done, ! findings, ? waiting, – skipped, ✕ failed. */
export type StageTone = 'done' | 'findings' | 'waiting' | 'skipped' | 'failed'

export function stageTone(stage: PipelineStage): StageTone {
  if (stage.status === 'failed') return 'failed'
  if (stage.status === 'waiting') return 'waiting'
  if (stage.status === 'skipped' || stage.status === 'cancelled') return 'skipped'
  if (stage.status === 'stopped') return 'findings'
  return (stage.evidence?.findings?.after?.length ?? 0) > 0 ? 'findings' : 'done'
}

/** Spoken name of each tone, for the icon's label. */
export const TONE_LABEL: Record<StageTone, string> = {
  done: '완료',
  findings: '확인할 것 있음',
  waiting: '답 기다림',
  skipped: '건너뜀',
  failed: '실패',
}

/** Whether a stage has anything behind 「보기」. */
export function hasEvidence(stage: PipelineStage): boolean {
  const e = stage.evidence
  if (!e) return false
  return Boolean(
    e.computed?.length || e.computedBlock || e.findings?.before?.length || e.findings?.after?.length ||
      e.changes?.length || e.code?.length || e.questions?.length,
  )
}

/**
 * The message whose stage list holds the open question card: the last assistant message, when
 * its run is waiting and the session's pending card is a clarify card. Elsewhere the card keeps
 * its old place in the transcript.
 */
export function waitingCardMessageId(session: Session | undefined): string | null {
  if (!session || session.pending?.stage !== 'clarify') return null
  const last = [...session.messages].reverse().find((m: Message) => m.role === 'assistant')
  if (!last?.run) return null
  return last.run.stages.some((s) => s.status === 'waiting') ? last.id : null
}

/** The six steps an agent's pipeline is drawn as, in order. */
export type ChainStep = 'material' | 'question' | 'compute' | 'write' | 'check' | 'export'

export const CHAIN_LABEL: Record<ChainStep, string> = {
  material: '자료',
  question: '질문',
  compute: '계산',
  write: '작성',
  check: '검사',
  export: '내보내기',
}

const CHAIN_ORDER: ChainStep[] = ['material', 'question', 'compute', 'write', 'check', 'export']

/** Which chain step a stage belongs to, by its kind and registry name. */
export function chainStepOf(stage: { name: string; kind: string }): ChainStep | null {
  const name = stage.name
  if (/gap|clarify|question/.test(name)) return 'question'
  if (/export/.test(name)) return 'export'
  if (/research|material|source|search|context/.test(name)) return 'material'
  if (stage.kind === 'check' || name.startsWith('check:')) return 'check'
  if (stage.kind === 'compute' || /calc|settle|prepass/.test(name)) return 'compute'
  if (stage.kind === 'model') return 'write'
  return null
}

/** The chain steps a pipeline has, in the fixed order. */
export function pipelineChain(pipeline: PipelineDefinition): ChainStep[] {
  const found = new Set<ChainStep>()
  for (const [flow, stages] of Object.entries(pipeline.flows ?? {})) {
    if (flow === 'export') found.add('export')
    for (const stage of stages ?? []) {
      const step = chainStepOf(stage)
      if (step) found.add(step)
    }
  }
  if (pipeline.options?.search) found.add('material')
  if (pipeline.checks?.length) found.add('check')
  return CHAIN_ORDER.filter((step) => found.has(step))
}

/** Flow names in reading order; unknown flows after the known ones. */
const FLOW_ORDER = ['pre', 'plan', 'write', 'make', 'revise', 'finish', 'export']

export function orderedFlows(pipeline: PipelineDefinition): string[] {
  const names = Object.keys(pipeline.flows ?? {})
  return [
    ...FLOW_ORDER.filter((name) => names.includes(name)),
    ...names.filter((name) => !FLOW_ORDER.includes(name)),
  ]
}

export const FLOW_LABEL: Record<string, string> = {
  pre: '시작 전 확인',
  plan: '구성 잡기',
  write: '작성',
  make: '만들기',
  revise: '고쳐 쓰기',
  finish: '답 마무리',
  export: '내보내기',
}

export const KIND_LABEL: Record<string, string> = {
  compute: '계산',
  model: '모델',
  check: '검사',
  io: '입출력',
}

/** Korean for the finding codes the check stages report; unknown codes show as they are. */
export const FINDING_LABEL: Record<string, string> = {
  ungrounded_value: '자료에 없는 값',
  ungrounded_number: '자료에 없는 수치',
  ungrounded_date: '자료에 없는 날짜',
  missing_fact: '빠진 정보',
  date_mismatch: '날짜가 맞지 않음',
  sum_mismatch: '합계가 맞지 않음',
  inconsistent_value: '값이 서로 다름',
}

/** Errors the rerun endpoint answers with, in words. */
export const RERUN_ERROR: Record<string, string> = {
  run_not_found: '이 실행 기록을 찾지 못했습니다.',
  stage_not_rerunnable: '이 단계부터는 다시 만들 수 없습니다.',
  run_not_rerunnable: '이 작업은 단계부터 다시 만들 수 없습니다.',
  session_busy: '다른 작업이 진행 중입니다. 끝난 뒤 다시 시도해 주세요.',
  insufficient_credits: '크레딧이 부족합니다.',
  model_unavailable: '이 작업에 쓴 모델을 지금은 쓸 수 없습니다.',
}

/**
 * The server's `reason` is short internal English; the person reads a Korean sentence. Known
 * reasons map by pattern, anything else by the stage's status. The raw text stays in the
 * expanded details.
 */
const REASONS: [RegExp, string][] = [
  [/^stopped at /, '앞 단계에서 멈춰 실행하지 않음'],
  [/timed? ?out|timeout/i, '시간이 초과됨'],
  [/^cancel/, '중단됨'],
  [/no check skills|no code checks/, '붙은 검사가 없음'],
  [/no document format/, '문서 서식이 정해지지 않음'],
  [/no pre-?pass/, '미리 계산할 것이 없음'],
  [/nothing to compute/, '계산할 것이 없음'],
  [/nothing changed|nothing rewritten/, '바뀐 곳이 없음'],
  [/no material to trace/, '대조할 자료가 없음'],
  [/no submit blocks/, '제출용 답안이 없음'],
  [/approved outline was given/, '승인한 구성을 그대로 씀'],
  [/approved outline is empty/, '승인한 구성이 비어 있음'],
  [/not the first draft/, '처음 쓰는 글이 아님'],
  [/not a revision/, '고쳐 쓰기 요청이 아님'],
  [/proposal offered/, '구성안을 먼저 보여 드림'],
  [/^needs:|missing facts/, '먼저 확인할 것이 있음'],
  [/outline/, '구성을 잡지 못함'],
  [/term not found/, '찾는 말이 문서에 없음'],
  [/a deck/, '슬라이드에는 해당 없음'],
  [/^no .* (on|for|to) /, '이번 요청에는 해당 없음'],
]

const STATUS_REASON: Partial<Record<PipelineStage['status'], string>> = {
  skipped: '이번 요청에는 필요 없음',
  failed: '내부 오류로 실패',
  stopped: '여기서 멈춤',
  waiting: '답을 기다림',
  cancelled: '중단됨',
}

export function stageReason(stage: PipelineStage): string {
  const raw = (stage.reason ?? '').trim()
  if (!raw) return STATUS_REASON[stage.status] ?? ''
  if (/[가-힣]/.test(raw)) return raw
  return REASONS.find(([pattern]) => pattern.test(raw))?.[1] ?? STATUS_REASON[stage.status] ?? ''
}
