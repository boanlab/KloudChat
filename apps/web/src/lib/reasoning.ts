import type { ModelInfo, ReasoningMode, RoutingMode, SessionKind, TurnReasoning } from '@/types'
import { isSuperMode } from '@/lib/superAgent'

/** The chip's three choices, in menu order. */
export const REASONING_MODES: readonly ReasoningMode[] = ['auto', 'fast', 'deep']

export const REASONING_LABEL: Record<ReasoningMode, string> = {
  auto: '자동',
  fast: '빠르게',
  deep: '깊게',
}

/** What each choice does, under Super Agent and on a model picked by hand. */
export const REASONING_HINT: Record<'super' | 'manual', Record<ReasoningMode, string>> = {
  super: {
    auto: '질문마다 Super Agent가 추론 강도를 정합니다',
    fast: '추론 없이 바로 답합니다',
    deep: '추론에 강한 모델이 깊게 생각하고 답합니다',
  },
  manual: {
    auto: '모델에 설정된 기본 추론으로 답합니다',
    fast: '추론을 끄거나 가장 낮게 답합니다',
    deep: '이 모델이 낼 수 있는 가장 높은 추론으로 답합니다',
  },
}

/** Where the chip shows: the surfaces whose composer sends a written turn. */
export function offersReasoning(kind: SessionKind): boolean {
  return kind === 'chat' || kind === 'report' || kind === 'slides'
}

/**
 * Whether the chip can change anything for this turn: Super Agent always can (it picks the
 * model); a model picked by hand only when its reasoning can be steered.
 */
export function reasoningSteerable(mode: RoutingMode | undefined, model: ModelInfo | undefined): boolean {
  if (isSuperMode(mode)) return true
  return model?.reasoningControl === true
}

/** 「깊게 · high」: the chip and the level the writer was sent, for the answer's line. */
export function reasoningLine(reasoning: TurnReasoning, t: (s: string) => string): string {
  const mode = t(REASONING_LABEL[reasoning.mode] ?? reasoning.mode)
  if (reasoning.unsupported) return `${mode} · ${t('이 모델은 조절 불가')}`
  const effort =
    reasoning.effort === 'default'
      ? t('모델 기본')
      : reasoning.effort === 'none'
        ? t('추론 끔')
        : reasoning.effort === 'on'
          ? t('추론 켬')
          : reasoning.effort
  return `${mode} · ${effort}`
}
