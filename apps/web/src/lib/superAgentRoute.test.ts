import { describe, expect, it } from 'vitest'
import { superRouteLabel } from './superAgent'
import type { SuperAgentRoute } from '@/types'

const base: SuperAgentRoute = {
  mode: 'super_strict', taskClass: 'reasoning', source: 'rule', reasonCode: 'reasoning_markers',
  selectedModel: 'strict-local/deepseek-v4-flash', effort: 'high', pick: 'rule', tools: 'full',
  routerVersion: 'v',
}
const label = (id: string) => ({
  'strict-local/deepseek-v4-flash': 'DeepSeek V4 Flash',
  'strict-local/qwen3.8-flash': 'Qwen3.8 Flash',
})[id] ?? id
const t = (s: string) => s

describe('superRouteLabel on the material row', () => {
  it('names the material row', () => {
    expect(superRouteLabel({ ...base, row: 'material' }, label, t).label).toBe(
      '이번 답: DeepSeek V4 Flash · 복잡한 추론 · 내부 자료라 자료에 강한 모델')
  })
  it('names the model that carried the turn on and the one that failed', () => {
    const route = { ...base, row: 'material' as const, fallbackModel: 'strict-local/qwen3.8-flash',
      fallbackReason: 'timeout' as const }
    expect(superRouteLabel(route, label, t).label).toBe(
      '이번 답: Qwen3.8 Flash · 복잡한 추론 · 내부 자료라 자료에 강한 모델 · DeepSeek V4 Flash 모델이 응답하지 않아 이어 씀')
  })
  it('says when a model that failed lately was passed over', () => {
    const route = { ...base, selectedModel: 'strict-local/qwen3.8-flash', pick: 'fallback',
      skipped: 'strict-local/deepseek-v4-flash' }
    expect(superRouteLabel(route, label, t).label).toContain('DeepSeek V4 Flash 모델은 최근 응답하지 않아 건너뜀')
  })
})
