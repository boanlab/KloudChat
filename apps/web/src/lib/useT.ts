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
  return (text: string) => translate(lang, text)
}
