import { useCallback, useMemo } from 'react'
import { useStore } from '@/store/useStore'
import { translateIn, translator, type Translator } from './i18n'

export type { Translator }

/** Translator for the current language; missing strings return the Korean. */
export function useT(): Translator {
  const lang = useStore((s) => s.lang)
  // Stable while the language is: effects that list `t` must not re-run every render.
  return useMemo(() => {
    return translator(() => lang)
  }, [lang])
}

/** A translator for words whose English depends on the screen: `useTIn('hub')('탐색')` → Explore. */
export function useTIn(context: string): (text: string) => string {
  const lang = useStore((s) => s.lang)
  return useCallback((text: string) => translateIn(lang, context, text), [lang, context])
}
