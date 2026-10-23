import { describe, expect, it } from 'vitest'
import { columnShares, em, sharedValue } from '@/components/slides/typeScale'

const head = ['Metric', '27B local', 'Frontier cloud']

describe('sharedValue', () => {
  it('draws one value every value column holds once across them', () => {
    expect(sharedValue(['Tasks', '40 prompts, 3 raters', '40 prompts, 3 raters'], 3)).toBe('40 prompts, 3 raters')
    expect(sharedValue(['Cost', '$4', '$38'], 3)).toBeNull()
  })

  it('keeps short marks, single value columns and short rows in their cells', () => {
    expect(sharedValue(['Offline', '✓', '✓'], 3)).toBeNull()
    expect(sharedValue(['Offline', 'N/A', 'N/A'], 3)).toBeNull()
    expect(sharedValue(['Plan', 'Shared'], 2)).toBeNull()
    expect(sharedValue(['Plan', 'Shared', 'Shared'], 4)).toBeNull()
  })
})

describe('columnShares', () => {
  it('counts a spanned value as half in each of its two columns', () => {
    const text = '40 document-drafting prompts, three human raters'
    const weights = head.map((cell, c) => Math.max(3, Math.min(22, Math.max(em(cell), c ? em(text) / 2 : em('Tasks')))) + 2)
    const total = weights.reduce((sum, weight) => sum + weight, 0)
    expect(columnShares([head, ['Tasks', text, text]])).toEqual(weights.map((weight) => weight / total))
  })
})
