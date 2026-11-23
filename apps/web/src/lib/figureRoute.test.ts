import { describe, expect, it } from 'vitest'
import { FIGURE_STYLES, modelDrawsFigure } from '@/lib/figureRoute'
import type { ModelInfo } from '@/types'

const model = (over: Partial<ModelInfo>) =>
  ({ id: 'm', kinds: ['image'], strictLocal: false, dataBoundary: 'external', ...over }) as ModelInfo

describe('a figure chip on the image screen', () => {
  it('is a figure style only for 구조도, 흐름도 and 개념도', () => {
    expect(Object.keys(FIGURE_STYLES).sort()).toEqual(['구조도', '개념도', '흐름도'].sort())
    expect(FIGURE_STYLES['차트']).toBeUndefined()
  })
  it('is drawn by an outside picture model itself', () => {
    expect(modelDrawsFigure(model({}))).toBe(true)
    expect(modelDrawsFigure(model({ dataBoundary: 'hybrid' }))).toBe(true)
  })
  it('is drawn by code for a model inside the institution or none picked', () => {
    expect(modelDrawsFigure(model({ strictLocal: true, dataBoundary: 'self_hosted' }))).toBe(false)
    expect(modelDrawsFigure(model({ dataBoundary: 'self_hosted' }))).toBe(false)
    expect(modelDrawsFigure(model({ kinds: ['chat'] }))).toBe(false)
    expect(modelDrawsFigure(undefined)).toBe(false)
  })
})
