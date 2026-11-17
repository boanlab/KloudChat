import { describe, expect, it } from 'vitest'
import type { ModelInfo } from '@/types'
import { answerRows, modelRows, policyText, runChip } from './runInfo'

const t = (s: string) => s
const base = { id: 'm', label: 'M', contextWindow: 127_000 } as ModelInfo

describe('runChip', () => {
  it('says nothing when nothing is declared', () => {
    expect(runChip(base, t)).toBeNull()
    expect(runChip({ ...base, runInfo: { zdr: true } }, t)).toBeNull()
  })
  it('shows the weights quantization of a self-hosted model', () => {
    expect(runChip({ ...base, runInfo: { quantization: 'NVFP4' } }, t)).toBe('NVFP4')
  })
  it('shows the pinned provider before anything else, and never an undisclosed quantization', () => {
    expect(runChip({ ...base, runInfo: { quantization: 'fp8', providerPinned: 'KT' } }, t)).toBe('제공자: KT')
    expect(runChip({ ...base, runInfo: { quantization: 'unknown' } }, t)).toBeNull()
  })
})

describe('modelRows', () => {
  it('lists what is declared, 미공개 for an unknown quantization, and the context', () => {
    const rows = modelRows({
      ...base,
      runInfo: { quantization: 'unknown', providerPinned: 'KT', zdr: false, policy: 'open_weight_strict' },
    }, t)
    expect(rows).toEqual([
      { label: '양자화 (고정 제공자)', value: '미공개' },
      { label: '제공자 고정', value: 'KT' },
      { label: 'ZDR', value: '아니요' },
      { label: '허용 대상', value: '양자화 공개·ZDR 엔드포인트' },
      { label: '최대 컨텍스트', value: '127.0k' },
    ])
  })
  it('lists the self-hosted facts', () => {
    const rows = modelRows({ ...base, runInfo: { quantization: 'NVFP4', kvCache: 'fp8', engine: 'vLLM 0.33.0' } }, t)
    expect(rows.map((r) => r.value)).toEqual(['NVFP4', 'fp8', 'vLLM 0.33.0', '127.0k'])
  })
})

describe('policyText', () => {
  it('has no 「만」 or 「only」', () => {
    expect(policyText('open_weight_strict', t)).toBe('양자화 공개·ZDR 엔드포인트')
    expect(policyText('zdr_only', t)).toBe('ZDR 엔드포인트')
    expect(policyText('pinned', t)).toBeNull()
  })
})

describe('answerRows', () => {
  it('shows provider, fallback and only the settings that were sent', () => {
    const rows = answerRows({
      model: 'x/y', provider: 'Wafer', fallback: false, quantization: 'fp8',
      call: { temperature: 0.4, seed: 0, reasoning: 'high', systemPromptHash: 'abc123' },
    }, (id) => `label ${id}`, t)
    expect(rows).toEqual([
      { label: '답한 모델', value: 'label x/y' },
      { label: '답한 제공자', value: 'Wafer' },
      { label: '양자화 (가중치)', value: 'fp8' },
      { label: '대체 모델로 답함', value: '아니요' },
      { label: 'temperature', value: '0.4' },
      { label: 'seed', value: '0' },
      { label: '추론 노력', value: 'high' },
      { label: '시스템 프롬프트 해시', value: 'abc123' },
    ])
  })
})
