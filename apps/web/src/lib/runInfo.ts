import type { AnswerRunInfo, ModelInfo, ModelRunInfo } from '@/types'
import { formatTokens } from '@/lib/utils'

type T = (s: string) => string

export type RunRow = { label: string; value: string }

/** 「unknown」 is what a provider says when it does not disclose; shown as 「미공개」. */
function quantizationText(q: string, t: T): string {
  return q.toLowerCase() === 'unknown' ? t('미공개') : q
}

/** What a policy allows, as a noun phrase (no 「만」: it is a status, not a promise of exclusivity). */
export function policyText(policy: string | undefined, t: T): string | null {
  if (policy === 'open_weight_strict') return t('양자화 공개·ZDR 엔드포인트')
  if (policy === 'zdr_only') return t('ZDR 엔드포인트')
  return null
}

/** The one short chip in the picker's row: the provider when pinned, else the weights' quantization
 *  of a model that declares it. Nothing when neither is known. */
export function runChip(m: ModelInfo, t: T): string | null {
  const info = m.runInfo
  if (!info) return null
  if (info.providerPinned) return t('제공자: {name}').replace('{name}', info.providerPinned.split('|').join(', '))
  if (info.quantization && info.quantization.toLowerCase() !== 'unknown') return info.quantization
  return null
}

function declaredRows(info: ModelRunInfo, t: T): RunRow[] {
  const rows: RunRow[] = []
  if (info.quantization) {
    rows.push({
      label: info.providerPinned ? t('양자화 (고정 제공자)') : t('양자화 (가중치)'),
      value: quantizationText(info.quantization, t),
    })
  }
  if (info.kvCache) rows.push({ label: t('KV 캐시'), value: info.kvCache })
  if (info.engine) rows.push({ label: t('서빙 엔진'), value: info.engine })
  if (info.providerPinned) {
    rows.push({ label: t('제공자 고정'), value: info.providerPinned.split('|').join(', ') })
  }
  if (typeof info.zdr === 'boolean') rows.push({ label: 'ZDR', value: info.zdr ? t('예') : t('아니요') })
  const policy = policyText(info.policy, t)
  if (policy) rows.push({ label: t('허용 대상'), value: policy })
  return rows
}

/** Whether the picker's ⓘ is worth showing: a quantization (not 「unknown」), KV cache or engine is
 *  declared. Context, pin and ZDR alone do not earn an icon. */
export function hasRunDetail(m: ModelInfo): boolean {
  const info = m.runInfo
  if (!info) return false
  return Boolean(
    (info.quantization && info.quantization.toLowerCase() !== 'unknown') || info.kvCache || info.engine,
  )
}

/** The picker's ⓘ panel: declared run facts, then the context window. */
export function modelRows(m: ModelInfo, t: T): RunRow[] {
  const rows = declaredRows(m.runInfo ?? {}, t)
  if (m.contextWindow) rows.push({ label: t('최대 컨텍스트'), value: formatTokens(m.contextWindow) })
  return rows
}

/** An answer's 「이번 답」 details: who answered, on what, with which call settings. */
export function answerRows(run: AnswerRunInfo, modelLabel: (id: string) => string, t: T): RunRow[] {
  const rows: RunRow[] = []
  if (run.model) rows.push({ label: t('답한 모델'), value: modelLabel(run.model) })
  if (run.provider) rows.push({ label: t('답한 제공자'), value: run.provider })
  rows.push(...declaredRows(run, t))
  rows.push({ label: t('대체 모델로 답함'), value: run.fallback ? t('예') : t('아니요') })
  if (typeof run.selfHostedShare === 'number') {
    rows.push({ label: t('자체 서버 비중'), value: `${Math.round(run.selfHostedShare * 100)}%` })
  }
  const stageName: Record<string, string> = { plan: '개요', hard: '어려운 부분', repair: '고쳐 쓰기' }
  for (const s of run.stages ?? []) {
    rows.push({
      label: t(stageName[s.stage] ?? s.stage),
      value: `${modelLabel(s.model)} · ${t('{n}회').replace('{n}', String(s.calls))} · ${formatTokens(s.inputTokens + s.outputTokens)} tokens`,
    })
  }
  const c = run.call ?? {}
  const settings: [string, number | string | undefined][] = [
    ['temperature', c.temperature],
    ['top_p', c.topP],
    ['seed', c.seed],
    ['max_tokens', c.maxTokens],
    ['presence_penalty', c.presencePenalty],
    [t('추론 노력'), c.reasoning],
  ]
  for (const [label, value] of settings) {
    if (value !== undefined) rows.push({ label, value: String(value) })
  }
  if (c.systemPromptHash) rows.push({ label: t('시스템 프롬프트 해시'), value: c.systemPromptHash })
  return rows
}
