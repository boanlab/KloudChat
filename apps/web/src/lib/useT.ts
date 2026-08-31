import { useCallback } from 'react'
import { useStore } from '@/store/useStore'
import { translate } from './i18n'

/**
 * Returns a function that translates a string into the current language.
 *
 *     const t = useT()
 *     <Button>{t('저장')}</Button>
 *
 * A string that is not in the dictionary comes back as the Korean it was
 * given, which is the intended fallback.
 */
export function useT(): (text: string) => string {
  const lang = useStore((s) => s.lang)
  // Stable while the language is. A fresh function on every render is a fresh
  // dependency on every render, and an effect that lists `t` then re-runs each
  // time — which on `/settings/access` meant fetching the access log, storing
  // it, re-rendering, and fetching it again, without end. The page never
  // finished loading and the API took a request per frame for as long as it
  // was open.
  //
  // Fixed here rather than at the three call sites: the next effect to list
  // `t` would otherwise be the next loop, and there is no reason for a
  // translator to change identity while the language does not.
  return useCallback((text: string) => translate(lang, text), [lang])
}
