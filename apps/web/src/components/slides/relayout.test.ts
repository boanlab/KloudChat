import { describe, expect, it } from 'vitest'
import type { Slide } from '@/types'
import { relayout } from './relayout'

const base = (extra: Partial<Slide>): Slide => ({ id: 's1', layout: 'bullets', title: '제목', ...extra })
const texts = (slide: Slide) => JSON.stringify(slide)

describe('relayout keeps the content', () => {
  it('keeps every metric on the metrics layout (no cut at four)', () => {
    const metrics: [string, string][] = [['1', 'a'], ['2', 'b'], ['3', 'c'], ['4', 'd'], ['5', 'e'], ['6', 'f']]
    const out = relayout(base({ layout: 'bands', bands: metrics }), 'metrics')
    expect(out.metrics).toHaveLength(6)
    expect(out.held).toBeUndefined()
  })

  it('keeps every bullet on the closing layout (no cut at three)', () => {
    const bullets = ['하나', '둘', '셋', '넷', '다섯']
    const out = relayout(base({ bullets }), 'closing')
    expect(out.bullets).toEqual(bullets)
  })

  it('holds a chart the new layout cannot draw, and gives it back on a chart layout', () => {
    const chart = { kind: 'bar' as const, categories: ['가', '나'], series: [{ name: '값', values: [1, 2] }] }
    const out = relayout(base({ layout: 'chart', chart, bullets: ['요약'] }), 'bullets')
    expect(out.chart).toBeUndefined()
    expect(out.held?.chart).toEqual(chart)
    expect(out.bullets).toEqual(['요약'])
    const back = relayout(out, 'chart')
    expect(back.chart?.categories).toEqual(['가', '나'])
    expect(back.held).toBeUndefined()
  })

  it('holds the text a conversion leaves out instead of dropping it', () => {
    // a big number shows one figure: the other pairs and the body go into its line
    const out = relayout(base({ layout: 'metrics', metrics: [['30%', '증가'], ['2배', '속도']], body: '출처: 내부 자료' }), 'big-number')
    for (const word of ['30%', '증가', '2배', '속도', '출처: 내부 자료']) expect(texts(out)).toContain(word)
  })

  it('never loses a line across a round of layout changes', () => {
    const start = base({ bullets: ['가: 첫째', '나: 둘째', '다: 셋째', '라: 넷째', '마: 다섯째'], body: '맺음말' })
    let slide = start
    for (const layout of ['cards-3', 'metrics', 'quote', 'table', 'closing', 'timeline', 'compare-2', 'bullets'] as Slide['layout'][]) {
      slide = relayout(slide, layout)
      for (const word of ['첫째', '둘째', '셋째', '넷째', '다섯째', '맺음말']) expect(texts(slide), layout).toContain(word)
    }
  })
})
