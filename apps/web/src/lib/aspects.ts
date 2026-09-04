import type { ModelInfo } from '@/types'

/** The ratios the composer offers, widest first; 16:9 is the default. */
export const ASPECTS = ['16:9', '9:16', '4:3', '1:1']

/**
 * The ratios this image model can draw, in the composer's order. A model that
 * lists none is offered all four; one that serves none of them gets 1:1.
 */
export function servedAspects(model: ModelInfo | undefined): string[] {
  const served = model?.aspects?.length
    ? ASPECTS.filter((a) => model.aspects?.includes(a))
    : ASPECTS
  return served.length ? served : ['1:1']
}

/** The ratio a request to this model is sent with; the stored preference is left as is. */
export function servedAspect(aspect: string, model: ModelInfo | undefined): string {
  const served = servedAspects(model)
  return served.includes(aspect) ? aspect : served[0]
}
