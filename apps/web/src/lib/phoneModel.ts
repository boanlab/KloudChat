import type { ModelInfo } from '@/types'

/** Phone chip: the name without family and boundary suffix, then where it runs (「27b · 로컬」). */
export function phoneModelParts(m: Pick<ModelInfo, 'name' | 'label' | 'strictLocal' | 'dataBoundary'>, t: (s: string) => string): { core: string; place: string } {
  const bare = (m.name || m.label).replace(/\s*\([^)]*\)\s*$/, '').trim()
  const words = bare.split(/\s+/)
  // 「Qwen3.8 27b」 → 「27b」; a single word, or a first word that is not a family name, stays.
  // 「Veo 3.1 Lite」 → 「3.1 Lite」 would hide what the model is: keep the family when the rest is only a version.
  const rest = words.slice(1).join(' ')
  const core =
    words.length > 1 && /^[A-Za-z]+[\d.]*$/.test(words[0]) && !/^[\d.]+(\s|$)/.test(rest) ? rest : bare
  const place = m.strictLocal
    ? t('내부 전용')
    : m.dataBoundary === 'self_hosted' || m.dataBoundary === 'hybrid'
      ? t('로컬')
      : m.dataBoundary === 'external'
        ? t('외부')
        : ''
  return { core, place }
}
