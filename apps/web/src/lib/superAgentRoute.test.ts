import { describe, expect, it } from 'vitest'
import { superRouteLabel } from './superAgent'
import type { SuperAgentRoute } from '@/types'

const base: SuperAgentRoute = {
  mode: 'super_strict', taskClass: 'reasoning', source: 'rule', reasonCode: 'reasoning_markers',
  selectedModel: 'strict-local/qwen3.8-27b', effort: 'high', pick: 'rule', tools: 'full',
  routerVersion: 'v',
}
const label = (id: string) => ({
  'strict-local/qwen3.8-27b': 'Qwen3.8 27B',
  'strict-local/qwen3.8-flash': 'Qwen3.8 Flash',
})[id] ?? id
const t = (s: string) => s

describe('superRouteLabel on the material row', () => {
  it('names the material row', () => {
    expect(superRouteLabel({ ...base, row: 'material' }, label, t).label).toBe(
      '이번 답: Qwen3.8 27B · 복잡한 추론 · 내부 자료라 자료에 강한 모델')
  })
  it('names the model that carried the turn on and the one that failed', () => {
    const route = { ...base, row: 'material' as const, fallbackModel: 'strict-local/qwen3.8-flash',
      fallbackReason: 'timeout' as const }
    expect(superRouteLabel(route, label, t).label).toBe(
      '이번 답: Qwen3.8 Flash · 복잡한 추론 · 내부 자료라 자료에 강한 모델 · Qwen3.8 27B 모델이 응답하지 않아 이어 씀')
  })
  it('says when a model that failed lately was passed over', () => {
    const route = { ...base, selectedModel: 'strict-local/qwen3.8-flash', pick: 'fallback',
      skipped: 'strict-local/qwen3.8-27b' }
    expect(superRouteLabel(route, label, t).label).toContain('Qwen3.8 27B 모델은 최근 응답하지 않아 건너뜀')
  })
})

describe('superRouteLabel on the complex document row', () => {
  const doc: SuperAgentRoute = {
    ...base, taskClass: 'document', source: 'document', reasonCode: 'document_surface', row: 'complex_document',
    complexReason: 'form', complexSignals: ['form'],
  }
  it('names the row and why the document counted as complex', () => {
    expect(superRouteLabel(doc, label, t).label).toBe('이번 답: Qwen3.8 27B · 복잡한 문서 · 양식을 따름')
    expect(superRouteLabel({ ...doc, complexReason: 'coverage' }, label, t).label).toContain('자료를 모두 다룸')
    expect(superRouteLabel({ ...doc, complexReason: 'tools' }, label, t).label).toContain('차트·그림 작업')
    expect(superRouteLabel({ ...doc, complexReason: 'long_source' }, label, t).label).toContain('긴 자료')
  })
  it('says why another model wrote it', () => {
    const waited = { ...doc, fallbackModel: 'strict-local/qwen3.8-flash', fallbackReason: 'queue' as const }
    expect(superRouteLabel(waited, label, t).label).toBe(
      '이번 답: Qwen3.8 Flash · 복잡한 문서 · 양식을 따름 · Qwen3.8 27B 모델 차례가 길어 대신 씀')
  })
  it('a simple document reads as before', () => {
    expect(superRouteLabel({ ...doc, row: undefined, complexReason: undefined }, label, t).label).toBe(
      '이번 답: Qwen3.8 27B · 보고서·슬라이드')
  })
})
