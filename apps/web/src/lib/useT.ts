import { useCallback } from 'react'
import { useStore } from '@/store/useStore'
import { translate } from './i18n'

/**
 * Returns a translator for the current language: `t('저장')`. A string not in
 * the dictionary comes back as the Korean it was given.
 */
export function useT(): (text: string) => string {
  const lang = useStore((s) => s.lang)
  // Stable while the language is: effects that list `t` must not re-run every render.
  return useCallback((text: string) => translate(lang, text), [lang])
}
