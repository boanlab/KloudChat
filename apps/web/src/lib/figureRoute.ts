import type { ModelInfo } from '@/types'

/** Image-screen style chips that draw a figure, by the figure kind the code renderer calls it. */
export const FIGURE_STYLES: Record<string, 'method' | 'flow' | 'concept' | undefined> = {
  구조도: 'method',
  흐름도: 'flow',
  개념도: 'concept',
}

/**
 * Whether the picked picture model draws a figure chip itself: an outside model that draws from a
 * prompt. A model inside the institution, or none picked, leaves the figure to the code renderer.
 */
export function modelDrawsFigure(model: ModelInfo | undefined | null): boolean {
  if (!model) return false
  if (model.strictLocal || model.dataBoundary === 'self_hosted') return false
  return model.kinds.includes('image')
}
