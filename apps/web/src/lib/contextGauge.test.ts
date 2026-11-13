import { describe, expect, it } from 'vitest'
import {
  compactTokens,
  estimateTokens,
  gaugeLevel,
  gaugePercent,
  summaryLimit,
  withDraft,
  type ContextUsage,
} from './contextGauge'

const base: ContextUsage = {
  model: 'local/qwen',
  statedWindow: 131_072,
  window: 131_072,
  foreignTokenizer: false,
  reserve: 8_192,
  used: 10_000,
  parts: { system: 3_000, instructions: 500, memory: 0, files: 1_500, history: 5_000, draft: 0, tools: 0 },
  summarised: false,
  summarisedMessages: 0,
  headroom: 100_000,
  lastTurn: null,
  routed: false,
  routedBasis: null,
}

describe('the context gauge', () => {
  it('counts text the way the server does: CJK 0.7, the rest 0.3 a character, rounded down, plus one', () => {
    expect(estimateTokens('')).toBe(0)
    expect(estimateTokens('가나다라마바사아자차')).toBe(8)
    expect(estimateTokens('abcdefghij')).toBe(4)
  })

  it('swaps the counted draft for the typed one', () => {
    const typed = withDraft(base, '가'.repeat(100))
    expect(typed.parts.draft).toBe(71 + 4)
    expect(typed.used).toBe(10_000 + 75)
    expect(withDraft(typed, '   ').used).toBe(10_000)
  })

  it('shows a whole percent of the window, at least one when anything is in use', () => {
    expect(gaugePercent(0, 131_072)).toBe(0)
    expect(gaugePercent(100, 131_072)).toBe(1)
    expect(gaugePercent(55_050, 131_072)).toBe(42)
    expect(gaugePercent(500_000, 131_072)).toBe(100)
    expect(gaugePercent(10, 0)).toBe(100)
  })

  it('warns near the summary limit and turns full where older turns would be cut', () => {
    const limit = summaryLimit(base)
    expect(limit).toBe(122_880)
    expect(gaugeLevel({ ...base, used: limit * 0.5 })).toBe('ok')
    expect(gaugeLevel({ ...base, used: Math.ceil(limit * 0.8) })).toBe('near')
    expect(gaugeLevel({ ...base, used: limit })).toBe('full')
    // already running on a summary: never shown as plenty of room
    expect(gaugeLevel({ ...base, used: 20_000, summarised: true })).toBe('near')
  })

  it('writes token counts compactly', () => {
    expect(compactTokens(850)).toBe('850')
    expect(compactTokens(1_000)).toBe('1k')
    expect(compactTokens(2_345)).toBe('2.3k')
    expect(compactTokens(54_321)).toBe('54k')
    expect(compactTokens(131_072)).toBe('131k')
    expect(compactTokens(1_048_576)).toBe('1M')
  })
})
