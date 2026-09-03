import type { ModelInfo } from '@/types'

/** The ratios the composer offers, widest first; 16:9 is the default. */
export const ASPECTS = ['16:9', '9:16', '4:3', '1:1']

/**
 * The ratios this image model can draw, in the composer's order.
 *
 * The catalogue says which — the OpenAI image models return a square whatever
 * is asked, so beside them the chip offers 1:1 and nothing else. A model that
 * says nothing is offered all four, as before.
 */
export function servedAspects(model: ModelInfo | undefined): string[] {
  const served = model?.aspects?.length
    ? ASPECTS.filter((a) => model.aspects?.includes(a))
    : ASPECTS
  return served.length ? served : ['1:1']
}

/**
 * The ratio a request to this model is actually sent with. The stored
 * preference is left alone, so it shows again the moment a model that can
 * draw it is picked.
 */
export function servedAspect(aspect: string, model: ModelInfo | undefined): string {
  const served = servedAspects(model)
  return served.includes(aspect) ? aspect : served[0]
}
